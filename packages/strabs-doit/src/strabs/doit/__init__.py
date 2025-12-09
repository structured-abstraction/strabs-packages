"""
strabs.doit - For when you just want to get stuff done.

Supports:
- Parallel execution of independent tasks
- Sequential dependencies with .then()
- Nested child tasks with .child()
- Background watchers with .watching() - retry until available, killed on parent complete

Example:
    from strabs.doit import doit, run

    # Simple parallel tasks
    doit([
        run("lint", "npm run lint"),
        run("typecheck", "npm run typecheck"),
    ])

    # Sequential dependencies (build runs, then test)
    doit([
        run("build", "npm run build").then("test", "npm test"),
    ])

    # Mixed parallel and sequential
    doit([
        run("build", "npm build").then("test", "npm test"),
        run("lint", "npm lint"),  # runs in parallel with build
    ])

    # Background watchers (retry + kill on complete)
    doit([
        run("Creating cluster", "talosctl cluster create ...")
            .watching("docker logs -f container1"),
    ])
"""

__version__ = "0.2.0"

import concurrent.futures
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text


class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()


@dataclass
class TaskResult:
    """Result of a completed task."""

    name: str
    status: TaskStatus
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    children: list["TaskResult"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == TaskStatus.SUCCESS


@dataclass
class RunConfig:
    """Configuration for the task runner."""

    max_workers: int = 4
    output_lines: int = 3
    error_lines: int = 20
    fail_fast: bool = False
    raise_on_failure: bool = True


class SubtaskError(Exception):
    """Raised when a subtask fails."""

    def __init__(self, task_name: str, exit_code: int, stderr: str):
        self.task_name = task_name
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(f"Task '{task_name}' failed with exit code {exit_code}")


class TaskBuilder:
    """Fluent builder for tasks with full nesting support."""

    def __init__(
        self,
        name: str,
        command: str | Callable[[], None],
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        *,
        retry: bool = False,
        kill_on_parent_complete: bool = False,
    ):
        self.name = name
        self.command = command
        self.env = env or {}
        self.cwd = cwd
        self.retry = retry
        self.kill_on_parent_complete = kill_on_parent_complete
        self.next: TaskBuilder | None = None
        self.children: list[TaskBuilder] = []
        self._root: TaskBuilder = self

    def then(
        self,
        name: str,
        command: str | Callable[[], None],
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> "TaskBuilder":
        """Chain another task to run after this one completes."""
        self.next = TaskBuilder(name, command, env, cwd)
        self.next._root = self._root
        return self.next

    def child(self, child_task: "TaskBuilder") -> "TaskBuilder":
        """Add a child task that runs alongside this task."""
        self.children.append(child_task)
        return self

    def watching(self, command: str) -> "TaskBuilder":
        """Add a background watcher (retries until ready, killed on complete)."""
        watcher = TaskBuilder(
            name=command,
            command=command,
            retry=True,
            kill_on_parent_complete=True,
        )
        self.children.append(watcher)
        return self


@dataclass
class _RunningTask:
    """Runtime state for an executing task."""

    name: str
    command: str | Callable[[], None]
    env: dict[str, str]
    cwd: Path | None
    retry: bool
    kill_on_parent_complete: bool
    children: list["_RunningTask"]

    # Runtime state
    status: TaskStatus = TaskStatus.PENDING
    output_lines: list[str] = field(default_factory=list)
    all_output: list[str] = field(default_factory=list)
    exit_code: int = -1
    start_time: float = 0.0
    end_time: float = 0.0
    error_msg: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _process: subprocess.Popen[str] | None = None
    _stopped: bool = False
    _thread: threading.Thread | None = None


def _create_running_task(builder: TaskBuilder) -> _RunningTask:
    """Convert TaskBuilder to _RunningTask recursively."""
    return _RunningTask(
        name=builder.name,
        command=builder.command,
        env=builder.env,
        cwd=builder.cwd,
        retry=builder.retry,
        kill_on_parent_complete=builder.kill_on_parent_complete,
        children=[_create_running_task(c) for c in builder.children],
    )


class _TaskRunner:
    """Runs a single task with its children."""

    def __init__(self, task: _RunningTask, output_lines: int = 3):
        self.task = task
        self.output_lines = output_lines

    def run(self) -> TaskResult:
        """Execute the task and its children."""
        self.task.status = TaskStatus.RUNNING
        self.task.start_time = time.time()

        # Start all children in background threads
        child_threads: list[tuple[_RunningTask, threading.Thread]] = []
        for child in self.task.children:
            runner = _TaskRunner(child, self.output_lines)
            thread = threading.Thread(target=runner.run, daemon=True)
            thread.start()
            child_threads.append((child, thread))

        # Run main task
        try:
            cmd = self.task.command
            if callable(cmd):
                self._run_callable(cmd)
            elif self.task.retry:
                self._run_with_retry(cmd)
            else:
                self._run_subprocess(cmd)
        finally:
            # Stop children that should be killed
            for child, thread in child_threads:
                if child.kill_on_parent_complete:
                    self._stop_task(child)
                thread.join(timeout=1.0)

        self.task.end_time = time.time()

        return TaskResult(
            name=self.task.name,
            status=self.task.status,
            exit_code=self.task.exit_code,
            stdout="\n".join(self.task.all_output),
            stderr=self.task.error_msg,
            duration_seconds=self.task.end_time - self.task.start_time,
            children=[self._get_child_result(c) for c in self.task.children],
        )

    def _get_child_result(self, child: _RunningTask) -> TaskResult:
        """Get result from a child task."""
        return TaskResult(
            name=child.name,
            status=child.status,
            exit_code=child.exit_code,
            stdout="\n".join(child.all_output),
            stderr=child.error_msg,
            duration_seconds=child.end_time - child.start_time if child.end_time else 0,
            children=[self._get_child_result(c) for c in child.children],
        )

    def _stop_task(self, task: _RunningTask) -> None:
        """Stop a task and its children."""
        task._stopped = True
        if task._process:
            try:
                os.killpg(os.getpgid(task._process.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        for child in task.children:
            self._stop_task(child)

    def _run_with_retry(self, cmd: str) -> None:
        """Run command with retry on failure."""
        while not self.task._stopped:
            self.task._process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )

            if self.task._process.stdout:
                for line in self.task._process.stdout:
                    if self.task._stopped:
                        break
                    line = line.rstrip("\n")
                    with self.task._lock:
                        self.task.all_output.append(line)
                        self.task.output_lines.append(line)
                        if len(self.task.output_lines) > self.output_lines:
                            self.task.output_lines.pop(0)

            self.task._process.wait()

            if self.task._stopped:
                break

            # Retry after delay
            time.sleep(0.5)

        self.task.status = TaskStatus.SUCCESS
        self.task.exit_code = 0

    def _run_subprocess(self, cmd: str) -> None:
        """Run a shell command."""
        env = {**os.environ, **self.task.env}
        cwd = str(self.task.cwd) if self.task.cwd else None

        self.task._process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
            text=True,
            bufsize=1,
            preexec_fn=os.setsid,
        )

        if self.task._process.stdout:
            for line in self.task._process.stdout:
                line = line.rstrip("\n")
                with self.task._lock:
                    self.task.all_output.append(line)
                    self.task.output_lines.append(line)
                    if len(self.task.output_lines) > self.output_lines:
                        self.task.output_lines.pop(0)

        self.task._process.wait()
        self.task.exit_code = self.task._process.returncode
        self.task.status = (
            TaskStatus.SUCCESS if self.task.exit_code == 0 else TaskStatus.FAILED
        )

    def _run_callable(self, cmd: Callable[[], None]) -> None:
        """Run a callable."""
        try:
            cmd()
            self.task.status = TaskStatus.SUCCESS
            self.task.exit_code = 0
        except Exception as e:
            self.task.status = TaskStatus.FAILED
            self.task.exit_code = 1
            self.task.error_msg = str(e)


class _DisplayRenderer:
    """Renders tasks with full recursive nesting and tree lines."""

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    TREE_BRANCH = "├── "
    TREE_LAST = "└── "
    TREE_PIPE = "│   "
    TREE_SPACE = "    "

    def __init__(
        self,
        tasks: list[_RunningTask],
        output_lines: int = 3,
        error_lines: int = 20,
    ):
        self.tasks = tasks
        self.output_lines = output_lines
        self.error_lines = error_lines
        self.console = Console()
        self._frame = 0

    def render(self) -> Group:
        """Render all tasks."""
        self._frame = (self._frame + 1) % len(self.SPINNER_FRAMES)
        lines: list[Text] = []
        for task in self.tasks:
            lines.extend(self._render_task(task, prefix="", is_last=True, is_root=True))
        return Group(*lines)

    def _render_task(
        self,
        task: _RunningTask,
        prefix: str,
        is_last: bool,
        is_root: bool = False,
    ) -> list[Text]:
        """Render a task and its children recursively with tree lines."""
        lines: list[Text] = []
        spinner = self.SPINNER_FRAMES[self._frame]

        # Task status line
        status_line = Text()
        if is_root:
            status_line.append("")
        else:
            status_line.append(prefix, style="dim")
            status_line.append(
                self.TREE_LAST if is_last else self.TREE_BRANCH, style="dim"
            )

        if task.status == TaskStatus.RUNNING:
            status_line.append(f"{spinner} ", style="blue bold")
            status_line.append(task.name, style="blue")
        elif task.status == TaskStatus.SUCCESS:
            status_line.append("✓ ", style="green bold")
            status_line.append(task.name, style="green")
            if task.end_time:
                duration = task.end_time - task.start_time
                status_line.append(f" ({duration:.1f}s)", style="dim")
        elif task.status == TaskStatus.FAILED:
            status_line.append("✗ ", style="red bold")
            status_line.append(task.name, style="red")
            if task.end_time:
                duration = task.end_time - task.start_time
                status_line.append(f" ({duration:.1f}s)", style="dim")
        else:
            status_line.append("○ ", style="dim")
            status_line.append(task.name, style="dim")

        lines.append(status_line)

        # Calculate child prefix
        if is_root:
            child_prefix = ""
            output_prefix = self.TREE_PIPE if task.children else self.TREE_SPACE
        else:
            child_prefix = prefix + (self.TREE_SPACE if is_last else self.TREE_PIPE)
            output_prefix = child_prefix + (
                self.TREE_PIPE if task.children else self.TREE_SPACE
            )

        # Task output
        if task.status == TaskStatus.RUNNING:
            for line in task.output_lines[-self.output_lines :]:
                output_line = Text()
                output_line.append(output_prefix, style="dim")
                output_line.append(line, style="dim")
                lines.append(output_line)
        elif task.status == TaskStatus.FAILED:
            for line in task.all_output[-self.error_lines :]:
                output_line = Text()
                output_line.append(output_prefix, style="dim")
                output_line.append(line, style="red dim")
                lines.append(output_line)
            if task.error_msg:
                error_line = Text()
                error_line.append(output_prefix, style="dim")
                error_line.append(task.error_msg, style="red")
                lines.append(error_line)

        # Children (after output, recursively)
        if task.status == TaskStatus.RUNNING:
            for i, child in enumerate(task.children):
                is_last_child = i == len(task.children) - 1
                lines.extend(self._render_task(child, child_prefix, is_last_child))

        return lines


def _flatten_chain(builder: TaskBuilder) -> list[TaskBuilder]:
    """Flatten a .then() chain into a list."""
    root = builder
    while root._root != root:
        root = root._root

    chain: list[TaskBuilder] = []
    current: TaskBuilder | None = root
    while current:
        chain.append(current)
        current = current.next
    return chain


def _run_tasks(
    tasks: Sequence[TaskBuilder],
    config: RunConfig | None = None,
) -> list[TaskResult]:
    """Execute tasks with live progress display."""
    if config is None:
        config = RunConfig()

    if not tasks:
        return []

    all_results: list[TaskResult] = []

    # Group by chain depth
    chains = [_flatten_chain(t) for t in tasks]
    max_depth = max(len(chain) for chain in chains)

    for depth in range(max_depth):
        tasks_at_depth: list[_RunningTask] = []
        for chain in chains:
            if depth < len(chain):
                tasks_at_depth.append(_create_running_task(chain[depth]))

        if not tasks_at_depth:
            continue

        results = _run_parallel(tasks_at_depth, config)
        all_results.extend(results)

        failed = [r for r in results if not r.ok]
        if failed:
            if config.fail_fast or config.raise_on_failure:
                raise SubtaskError(
                    failed[0].name,
                    failed[0].exit_code,
                    failed[0].stderr or failed[0].stdout,
                )
            break

    return all_results


def _run_parallel(tasks: list[_RunningTask], config: RunConfig) -> list[TaskResult]:
    """Run tasks in parallel with live display."""
    results: dict[str, TaskResult] = {}
    runners = [_TaskRunner(task, config.output_lines) for task in tasks]
    renderer = _DisplayRenderer(tasks, config.output_lines, config.error_lines)

    with Live(
        renderer.render(), refresh_per_second=10, console=renderer.console
    ) as live:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=config.max_workers
        ) as executor:
            futures = {
                executor.submit(runner.run): runner.task.name for runner in runners
            }

            while futures:
                live.update(renderer.render())

                done, _ = concurrent.futures.wait(
                    futures.keys(),
                    timeout=0.1,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for future in done:
                    task_name = futures.pop(future)
                    try:
                        result = future.result()
                        results[task_name] = result

                        if config.fail_fast and not result.ok:
                            for f in futures:
                                f.cancel()
                            raise SubtaskError(
                                result.name,
                                result.exit_code,
                                result.stderr or result.stdout,
                            )
                    except SubtaskError:
                        raise
                    except Exception as e:
                        results[task_name] = TaskResult(
                            name=task_name,
                            status=TaskStatus.FAILED,
                            exit_code=1,
                            stdout="",
                            stderr=str(e),
                            duration_seconds=0.0,
                        )

        live.update(renderer.render())

    return [results[task.name] for task in tasks]


def run(
    name: str,
    command: str | Callable[[], None],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> TaskBuilder:
    """
    Create a task.

    Usage:
        run("name", "command")
        run("name", "command", env={"FOO": "bar"})

        # Chain with .then() for sequential
        run("build", "npm build").then("test", "npm test")

        # Add watchers
        run("server", "npm start").watching("tail -f log")
    """
    return TaskBuilder(name, command, env, cwd)


def doit(
    tasks: Sequence[TaskBuilder],
    config: RunConfig | None = None,
) -> list[TaskResult]:
    """
    Execute tasks.

    Usage:
        doit([run("foo", "cmd1"), run("bar", "cmd2")])

        # Chain with .then() for sequential
        doit([run("build", "npm build").then("test", "npm test")])

        # Mixed parallel and sequential
        doit([
            run("build", "npm build").then("test", "npm test"),
            run("lint", "npm lint"),  # parallel with build
        ])

        # Add watchers
        doit([run("server", "npm start").watching("tail -f log")])
    """
    return _run_tasks(tasks, config)
