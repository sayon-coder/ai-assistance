Place offline speech models here.

Speech recognition (default, required unless sherpa-onnx is installed):
  models/vosk-model-small-en-us-0.15
  Download from https://alphacephei.com/vosk/models
  or run: python -m talcon.model_setup

Optional streaming recognition engine (Apache-2.0):
  models/sherpa-onnx-streaming-zipformer-en-2023-06-26
  Install the package: pip install sherpa-onnx
  Download the model:  python -m talcon.model_setup --sherpa
  Vosk is used automatically when this model is absent.

Neural voice (required for speech, free, offline, Apache-2.0):
  models/kokoro/kokoro-v1.0.int8.onnx
  models/kokoro/voices-v1.0.bin
  Download with: python -m talcon.model_setup --kokoro
  Source: https://github.com/thewh1teagle/kokoro-onnx
  This is TALCON's only speech-synthesis engine; no Microsoft voice is used.
