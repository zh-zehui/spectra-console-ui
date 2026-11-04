# Project-local traditional segmentation source

These modules were copied from the existing `usrp_client` project and are used by `src/spectrum_agent/dsp.py`. The original client installation is kept separate. This directory is not a complete copy of that client application.

The inactive Qt receiver and its old machine-specific README are omitted from the public deployment snapshot. Headless RF acquisition is implemented in `src/spectrum_agent/acquisition.py`; the independent visual interface is served by `src/spectrum_agent/ui_gateway.py`.
