# Build the Python runtime independently of the Electron shell.
from pathlib import Path
from importlib.metadata import distribution
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root=Path(SPECPATH).parent
datas=[]
for directory in ('assets','docs','examples','app/web/templates'):
    datas.append((str(root/directory),directory))
datas.append((str(root/'app/pipeline/view_template_presets.json'),'app/pipeline'))
datas.extend((str(path),'scripts') for path in (root/'scripts').glob('*.mjs'))
for name in ('numpy','opencv-python','opencv-python-headless','Pillow','plyfile','torch','torchvision',
             'pycolmap','nerfstudio','wgpu','onnxruntime-directml'):
    try:datas+=copy_metadata(name)
    except Exception:pass
datas+=collect_data_files('wgpu')
hidden=collect_submodules('app')+collect_submodules('pycolmap')
binaries=collect_dynamic_libs('wgpu')+collect_dynamic_libs('onnxruntime')
# New torchvision loads _C_stable via torch.ops.load_library, rather than import.
# The standard hook only knows _C, so explicitly collect native ops and their DLLs.
binaries+=collect_dynamic_libs('torchvision')
binaries.extend((str(path),'torchvision') for path in
                Path(distribution('torchvision').locate_file('torchvision')).glob('*.pyd'))
a=Analysis([str(root/'desktop/backend_entry.py')],pathex=[str(root)],binaries=binaries,datas=datas,
           hiddenimports=hidden,excludes=['nerfstudio','gsplat','matplotlib','IPython','jupyter',
           'tensorboard','tensorflow','triton','pytest','tkinter','PyQt5','PyQt6','PySide6','scipy.tests'],
           noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='studio360-backend',console=True,upx=False)
coll=COLLECT(exe,a.binaries,a.datas,name='backend',upx=False)
