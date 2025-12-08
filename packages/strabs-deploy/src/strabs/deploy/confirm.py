"""Confirmation utilities."""

import random
import string

from rich.console import Console

console = Console()


def random_char_confirm(prompt: str) -> bool:
    chars = "".join(random.choices(string.ascii_lowercase, k=3))
    console.print(f"[bold yellow]{prompt}[/bold yellow]")
    response = console.input(
        f"Type '[bold magenta]{chars}[/bold magenta]' to confirm: "
    ).strip()
    if response != chars:
        console.print("[red]Aborted.[/red]")
        return False
    return True
