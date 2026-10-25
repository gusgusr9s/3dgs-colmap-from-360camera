"""Extended real-browser scene checks, called by test_scene_browser.py."""
import json
import math
import struct
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from scene_test_server import fixture_glb


def run_workflow(page,browser,base,out,state,frames,check,drag):
    def fill(selector,value):
        if page.locator('#export-dialog').get_attribute('open') is not None:page.keyboard.press('Escape')
        if selector.startswith(('#position-','#rotation-','#item-')):page.locator('#tab-object').click()
        page.locator(selector).fill(str(value));page.locator(selector).press('Tab')
    def select(name):
        page.locator('#tab-object').click()
        page.locator('.object-select').filter(has_text=name).click();frames()
    def world(identifier):
        return next(box for box in page.evaluate('window.sceneEditor.worldColliders') if box['id']==identifier)
    boxes=state()['colliders'];wall=next(box for box in boxes if box['kind']=='wall');floor=next(box for box in boxes if box['kind']=='floor')
    check('generated boxes belong to selected model',all(box['parent']=='qa-room-a' for box in boxes))
    initial=world(wall['id']);fill('#position-0',3)
    check('model translation moves its colliders',abs(world(wall['id'])['position'][0]-initial['position'][0]-3)<1e-5)
    fill('#rotation-1',45);fill('#item-scale',2)
    check('model scale changes world collider size',math.dist(world(wall['id'])['size'],[2*v for v in initial['size']])<1e-5)
    select(wall['name']);attached=world(wall['id']);page.locator('#collider-parent').select_option('')
    check('detaching collider preserves world position',math.dist(attached['position'],world(wall['id'])['position'])<1e-5)
    select('Room A (blue)');fill('#position-0',4)
    check('detached collider stays in world coordinates',math.dist(attached['position'],world(wall['id'])['position'])<1e-5)
    select(wall['name']);page.locator('#collider-parent').select_option('qa-room-a')
    check('reattaching collider preserves world position',math.dist(attached['position'],world(wall['id'])['position'])<1e-5)
    select('Room A (blue)');fill('#position-0',0);fill('#rotation-1',0);fill('#item-scale',1)
    select('Room B (warm)');page.locator('#tab-align').click();page.locator('#align-target').select_option('qa-room-a')
    for source,target in [([4.2,0,0],[0,0,0]),([6.2,0,0],[2,0,0]),([4.2,2,0],[0,2,0])]:
        for side,values in [('source',source),('target',target)]:
            for index,value in enumerate(values):fill(f'#align-{side}-{index}',value)
        page.locator('#align-add').click()
    page.locator('#align-apply').click()
    moving=lambda:next(item for item in state()['objects'] if item['id']=='qa-room-b')
    check('three point pairs align two models',math.dist(moving()['position'],[0,0,0])<1e-5)
    page.locator('#scene-undo').click();check('alignment is one undo step',abs(moving()['position'][0]-4.2)<1e-5)
    page.locator('#scene-redo').click();check('alignment redo restores solution',math.dist(moving()['position'],[0,0,0])<1e-5)
    page.locator('#scene-undo').click();select('Room A (blue)');page.locator('#tab-align').click()
    for name,point in [('a',[-1.3,0,1.3]),('b',[1.3,0,1.3])]:
        page.locator('#calibration-'+name).click();frames()
        screen=page.evaluate('p=>window.sceneEditor.projectPoint("qa-room-a",p)',point)
        page.mouse.click(*screen);frames()
    points=page.evaluate('window.sceneEditor.measurement')
    check('viewport picking records two calibration points',len(points)==2 and all(points))
    distance=math.dist(points[0]['local'],points[1]['local']);fill('#calibration-distance',distance*2)
    before_floor=world(floor['id'])['size'];page.locator('#calibration-apply').click()
    check('calibration scales scene and attached floor once',state()['units']=='meters' and abs(state()['objects'][0]['scale']-2)<1e-5 and math.dist(world(floor['id'])['size'],[2*v for v in before_floor])<1e-5)
    page.locator('#scene-undo').click();check('calibration can be undone',abs(state()['objects'][0]['scale']-1)<1e-5)
    page.locator('#scene-redo').click();check('calibration can be redone',abs(state()['objects'][0]['scale']-2)<1e-5)
    page.locator('#scene-undo').click()
    page.locator('#align-clear').click();page.locator('#precision-options > summary').click();page.locator('#snap-enabled').check();fill('#snap-distance',.5);page.locator('#precision-options > summary').click()
    page.locator('#transform-translate').click();drag('x',70,0)
    x=state()['objects'][0]['position'][0]
    check('actual mouse drag snaps to distance increments',abs(x)>.1 and abs(x/.5-round(x/.5))<1e-5)
    page.locator('#scene-undo').click();page.locator('#precision-options > summary').click();page.locator('#snap-enabled').uncheck();page.locator('#precision-options > summary').click()
    prop=out/'qa-prop.glb';prop.write_bytes(fixture_glb())
    page.locator('#library-open').click();page.locator('#glb-upload').set_input_files(str(prop))
    page.wait_for_function('window.sceneEditor.loaded===3',timeout=30000);frames(20)
    check('uploaded GLB renders beside Gaussian models',len(state()['objects'])==3 and state()['objects'][-1]['asset']['kind']=='glb')
    fill('#position-0',2);fill('#position-1',.5)
    page.locator('#export-open').click();page.locator('#glb-start').click();page.locator('#glb-download').wait_for(state='visible',timeout=60000)
    with page.expect_download() as glb_download:page.locator('#glb-download').click()
    glb=out/'scene-export.glb';glb_download.value.save_as(str(glb))
    payload=glb.read_bytes();length=struct.unpack_from('<I',payload,12)[0];document=json.loads(payload[20:20+length])
    check('scene GLB includes both Gaussian models and ordinary mesh',len(document['scenes'][0]['nodes'])==3 and 'KHR_gaussian_splatting' in document['extensionsRequired'])
    check('scene GLB separates collision geometry from visual models',len(document['scenes'][1]['nodes'])==5)
    viewer=browser.new_page(viewport={'width':1200,'height':850});glb_errors=[];glb_blocked=[]
    viewer.on('pageerror',lambda error:glb_errors.append(str(error)))
    def viewer_local_only(route):
        if route.request.url.startswith((base+'/', 'blob:', 'data:')):route.continue_()
        else:glb_blocked.append(route.request.url);route.abort()
    viewer.route('**/*',viewer_local_only)
    try:
        viewer.goto(base+page.locator('#glb-viewer').get_attribute('href'))
        viewer.wait_for_function('document.body.dataset.ready==="true" || document.body.dataset.error',timeout=60000)
        check('native Three.js renders exported scene GLB without external requests',viewer.locator('body').get_attribute('data-ready')=='true' and not glb_errors and not glb_blocked)
        check('scene GLB preserves every Gaussian',int(viewer.locator('body').get_attribute('data-splats'))==21878)
        check('scene GLB retains ordinary mesh props',int(viewer.locator('body').get_attribute('data-meshes'))==1)
        viewer.screenshot(path=str(out/'scene-glb-viewer.png'))
    finally:viewer.close()
    restored=browser.new_page()
    try:
        restored.goto(base+'/scene/'+state()['id']);restored.wait_for_function('window.sceneEditor?.ready')
        restored.locator('#export-open').click();restored.locator('#glb-download').wait_for(state='visible',timeout=15000)
        check('export dialog restores saved GLB download without rebuilding',restored.locator('#glb-download').get_attribute('href')==page.locator('#glb-download').get_attribute('href') and restored.locator('#glb-start').is_hidden())
    finally:restored.close()
    page.locator('#package-start').click();page.locator('#package-download').wait_for(state='visible',timeout=60000)
    with page.expect_download() as download:page.locator('#package-download').click()
    archive=out/'scene-portable.zip';download.value.save_as(str(archive))
    fill('#position-2',1)
    check('editing scene hides obsolete ZIP download',page.locator('#package-download').get_attribute('hidden') is not None)
    check('editing scene hides obsolete GLB download',page.locator('#glb-download').get_attribute('hidden') is not None)
    page.locator('#scene-undo').click()
    portable=out/('portable-'+state()['id']);portable.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not (portable/name).resolve().is_relative_to(portable.resolve()):raise AssertionError('unsafe ZIP member')
        bundle.extractall(portable)
    package=json.loads((portable/'scene.json').read_text(encoding='utf8'))
    check('ZIP embeds all three models and viewer',len(package['assets'])==3 and (portable/'index.html').is_file() and all((portable/asset['url']).is_file() for asset in package['assets']))
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,directory=str(portable)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    offline=browser.new_page(viewport={'width':1440,'height':950});errors=[];blocked=[]
    offline.on('pageerror',lambda error:errors.append(str(error)))
    origin=f'http://127.0.0.1:{server.server_port}'
    def local_only(route):
        if route.request.url.startswith((origin+'/', 'blob:', 'data:')):route.continue_()
        else:blocked.append(route.request.url);route.abort()
    offline.route('**/*',local_only)
    try:
        offline.goto(origin+'/');offline.wait_for_function('window.scenePlayer?.loaded===3',timeout=60000)
        check('portable viewer loads with original server blocked',not errors and not blocked)
        offline.locator('#walk').click();offline.locator('#canvas').focus()
        position=offline.evaluate('window.scenePlayer.position');offline.keyboard.down('w')
        offline.evaluate('()=>new Promise(resolve=>{let n=0;function tick(){if(++n===30)resolve();else requestAnimationFrame(tick)}requestAnimationFrame(tick)})')
        offline.keyboard.up('w');after=offline.evaluate('window.scenePlayer.position')
        check('portable viewer walks with collision enabled',offline.evaluate('window.scenePlayer.walking') and math.dist(position,after)>.01 and after[1]>-1)
        offline.locator('#reset').click();offline.screenshot(path=str(out/'portable-viewer.png'),full_page=True)
    finally:offline.close();server.shutdown();server.server_close()
