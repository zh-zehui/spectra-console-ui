"""Keep the existing GNU Radio environment read-only despite its stale Python CMake path."""
from pathlib import Path
p=Path('/home/sribd/spectrum_agent/vendor/gr-ieee802-15-4/CMakeLists.txt')
s=p.read_text()
if 'set(ENABLE_PYTHON OFF)' not in s:
    anchor='find_package(Gnuradio "3.9" REQUIRED COMPONENTS blocks)'
    if anchor not in s: raise RuntimeError('Pinned CMake source anchor missing')
    s=s.replace(anchor,'set(ENABLE_PYTHON OFF)\n'+anchor+'\nset(PYTHON_EXECUTABLE "/home/sribd/spectrum_agent/.venv/bin/python" CACHE FILEPATH "" FORCE)\nset(ENABLE_PYTHON ON)\ninclude(GrPython)')
    p.write_text(s)
