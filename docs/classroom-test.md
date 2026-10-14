# Classroom test kit

The classroom scripts run the prototype with Ollama on CPU. Candidate models are listed in `scripts/classroom/config.env`; the scripts try them in order and keep downloaded binaries, models, and output under the local kit directory.

## Ubuntu

```bash
bash scripts/classroom/run_test.sh 1
```

Use option `2` for the larger agent plus embeddings. It requires the free-memory threshold configured in `config.env`.

## Windows

Run `scripts/classroom/run_test.bat` or pass `2` from a terminal. Python 3.10 or newer is required.

The script writes diagnostic output under `results/`. These files and the downloaded `ollama/` and `models/` directories are ignored by Git.
