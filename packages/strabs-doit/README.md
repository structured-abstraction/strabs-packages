# strabs-doit

For when you just want to get stuff done. Stop overthinking. Stop planning. Just do it.

![Do it](https://media.giphy.com/media/wi8Ez1mwRcKGI/giphy.gif) [![asciicast](https://asciinema.org/a/CNvT2pVZAVDhtJaEdaBYqD5hy.svg)](https://asciinema.org/a/CNvT2pVZAVDhtJaEdaBYqD5hy)

```bash
pip install strabs-doit
```

```python
from strabs.doit import doit, run

# Parallel tasks - because waiting is for chumps
doit([
    run("lint", "npm run lint"),
    run("typecheck", "npm run typecheck"),
])

# Sequential when you have to
doit([
    run("build", "npm run build").then("test", "npm test"),
])

# Both at once - live a little
doit([
    run("build", "npm build").then("test", "npm test"),
    run("lint", "npm lint"),
])
```
