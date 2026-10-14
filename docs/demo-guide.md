# Demo guide

The live demo is `demo.py`. It discovers an Ollama server on the local network, shows the retrieved documents, and streams one Spanish answer per question.

## Run

```bash
python demo.py
python demo.py --url http://IP:11434
python demo.py --no-model
```

Use `--today 2026-10-01` to demonstrate deadline calculations. Type `salir` to stop. Human-support requests and questions with no usable documents are handled without an LLM call.

The laptop must expose Ollama on the network (`OLLAMA_HOST=0.0.0.0`) and both computers should be on the same private mobile hotspot. The university Wi-Fi may isolate devices.
