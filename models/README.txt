Place offline speech models here.

Speech recognition (required):
  models/vosk-model-small-en-us-0.15
  Download from https://alphacephei.com/vosk/models
  or run: python -m talcon.model_setup

Neural voice (required for speech, free, offline, Apache-2.0):
  models/kokoro/kokoro-v1.0.int8.onnx
  models/kokoro/voices-v1.0.bin
  Download with: python -m talcon.model_setup --kokoro
  Source: https://github.com/thewh1teagle/kokoro-onnx
  This is TALCON's only speech-synthesis engine; no Microsoft voice is used.
