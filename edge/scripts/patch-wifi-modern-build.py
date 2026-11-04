from pathlib import Path
root=Path('/home/sribd/spectrum_agent')
p=root/'vendor/gr-wifi-modern/CMakeLists.txt'
text=p.read_text()
marker='# Spectrum local interpreter fix'
if marker not in text:
    anchor='include(GrVersion)'
    assert text.count(anchor)==1
    text=text.replace(anchor,marker+'\nset(PYTHON_EXECUTABLE "'+str(root/'.venv/bin/python')+'" CACHE FILEPATH "Spectrum Python" FORCE)\nset(ENABLE_PYTHON ON)\n'+anchor)
    p.write_text(text)
