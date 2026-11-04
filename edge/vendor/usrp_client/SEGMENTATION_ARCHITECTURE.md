# Segmentation plugin architecture

`segmentation_engineer_receiver.py` is the transport coordinator. It does not
contain model code, traditional detection code, or algorithm-specific command
line arguments.

## Runtime flow

```text
5560/5561 STFT PUB
        |
        v
segmentation_engineer_receiver.py
  decode -> per-stream ring buffer -> window scheduling
        |
        v
segmentation_registry.create_algorithm()
        |
        +--> segmentation_model.py
        +--> segmentation_traditional.py
        +--> future plugin modules
        |
        v
event-only JSON PUB (5571 -> client -> 5559)
```

传统算法（能量图）逐步说明见同目录 [`segmentation_traditional.md`](./segmentation_traditional.md)。
Web 瀑布叠加链路见仓库 [`docs/waterfall-pipeline.md`](../../docs/waterfall-pipeline.md)。

The receiver never sends `stft_frame_db` in the segmentation payload. STFT
display/upload remains an independent link.

## Optional local viewer

`segmentation_viewer.py` is a separate debugging GUI. It subscribes to the
STFT PUB endpoint(s) and the receiver's result PUB endpoint, then overlays the
window-relative event boxes on the local waterfall. It never creates an
algorithm instance and never publishes data:

```powershell
python segmentation_viewer.py `
  --stft-source tcp://127.0.0.1:5560 `
  --segmentation-source tcp://127.0.0.1:5571
```

For dual STFT streams use `--stft-source-ch0`, `--stft-source-ch1`, and the
matching stream-id options. The viewer is optional; the production web UI can
continue to display the independent STFT upload and server-side overlays.

## Algorithm parameters

Common transport parameters stay on the receiver command line:

```powershell
python segmentation_engineer_receiver.py `
  --stft-source tcp://127.0.0.1:5560 `
  --window-size 100 `
  --window-stride 10 `
  --algorithm traditional `
  --algorithm-config segmentation_config.json
```

Algorithm parameters are kept in the JSON file:

```json
{
  "default_algorithm": "traditional",
  "plugin_modules": [],
  "algorithms": {
    "traditional": {
      "params": {
        "cfar_threshold_db": 6.0,
        "min_event_frames": 2
      }
    },
    "model": {
      "params": {
        "weights": "...",
        "device": "cuda:0"
      }
    }
  }
}
```

The receiver selects a plugin name, extracts only that plugin's `params`, and
passes the dictionary to its constructor. A plugin owns validation, defaults,
resource initialization, and detection logic for its own parameters.

## Adding an algorithm

Create a module implementing the shared contract:

```python
from segmentation_contracts import SegmentationRequest
from segmentation_registry import register_algorithm


class MyPlugin:
    name = "my_algorithm"

    def __init__(self, params: dict):
        self.threshold = float(params.get("threshold", 3.0))

    def detect(self, request: SegmentationRequest) -> list[dict]:
        # Return structured events with at least box/label/score when useful.
        return []

    def close(self) -> None:
        pass


register_algorithm("my_algorithm", MyPlugin)
```

Then add the module name to `plugin_modules` and its parameters to
`algorithms.my_algorithm.params`. No receiver edit is needed.
