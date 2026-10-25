"""Real Chromium smoke checks for the scene editor's GPU and mouse controls.

Start scripts/scene_test_server.py first. This runner creates a new scene copy in
that isolated fixture workspace, leaving earlier fixture edits intact.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'tools/browser-qa'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=QA/'scene-test-workspace/fixture.json')
    parser.add_argument('--chromium', type=Path, default=QA/'browsers/chromium-1248/chrome-win64/chrome.exe')
    parser.add_argument('--headed', action='store_true')
    parser.add_argument('--full-workflow', action='store_true')
    parser.add_argument('--keep-open', action='store_true', help='Leave the tested Chromium window open until it is closed')
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding='utf8'))
    workspace = Path(fixture['workspace']).resolve()
    if not workspace.is_relative_to(QA.resolve()) or not (workspace/'.scene-test-fixture.json').is_file():
        raise ValueError('Use a marked, isolated scene-test fixture workspace')
    base = fixture['url'].split('/scene/')[0]
    source = json.loads(urlopen(fixture['url'].replace('/scene/', '/api/scenes/')).read())
    # Always start from the same known transforms without overwriting any scene.
    source['name'] = 'Chromium smoke: gizmos, resets and automatic collision boxes'
    source['objects'][0].update(position=[0,0,0],rotation=[0,0,0],scale=1,visible=True)
    source['objects'][1].update(position=[4.2,0,0],rotation=[0,0,0],scale=1,visible=True)
    source['colliders'] = []
    source['view'] = {'position':[8.5,6,12],'target':[2.1,1,0]}
    request = Request(base+'/api/scenes',data=json.dumps(source).encode(),headers={'Content-Type':'application/json'})
    scene = json.loads(urlopen(request).read())
    url = base+'/scene/'+scene['id']
    out = workspace/'review'; out.mkdir(exist_ok=True)
    report = {'url':url,'checks':[],'page_errors':[],'console_errors':[],'http_errors':[]}

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(args.chromium),headless=not (args.headed or args.keep_open))
        report['browser'] = browser.version
        page = browser.new_page(viewport={'width':1600,'height':1050},device_scale_factor=1,accept_downloads=True)
        page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        page.on('console',lambda message:report['console_errors'].append(message.text) if message.type=='error' else None)
        page.on('response',lambda response:report['http_errors'].append([response.status,response.url]) if response.status>=400 else None)
        state = lambda:page.evaluate('window.sceneEditor.snapshot()')
        selected = lambda:next(item for item in state()['objects'] if item['id']=='qa-room-a')
        def check(name, condition):
            if not condition:
                raise AssertionError(name)
            report['checks'].append(name)
            print(name,flush=True)
        def frames(count=3):
            page.evaluate('(count)=>new Promise(resolve=>{let n=0;const next=()=>++n>=count?resolve():requestAnimationFrame(next);requestAnimationFrame(next)})',count)
        def drag(axis, dx, dy, cancel=False):
            frames()
            screen = page.evaluate('window.sceneEditor.gizmoScreen')
            point = screen[axis]
            page.mouse.move(*point)
            page.mouse.down()
            page.mouse.move(point[0]+dx,point[1]+dy,steps=12)
            check('mouse drag engages '+axis+' gizmo',page.evaluate('window.sceneEditor.dragging'))
            if cancel: page.keyboard.press('Escape')
            page.mouse.up()
            frames()

        try:
            page.goto(url)
            page.wait_for_function('window.sceneEditor?.ready && window.sceneEditor.loaded===2',timeout=30000)
            # Tiny LoD and depth sorting complete after the model load promise.
            frames(30)
            check('both Gaussian models loaded in Chromium',not report['page_errors'])
            page.locator('#view-options > summary').click();page.locator('#show-grid').uncheck();page.locator('#view-options > summary').click()
            page.locator('.object-select').filter(has_text='Room A (blue)').click()
            frames()
            model_origin=page.evaluate('window.sceneEditor.gizmoScreen.origin')
            canvas=page.locator('#scene-canvas').bounding_box()
            page.mouse.click(canvas['x']+canvas['width']/2,canvas['y']+25)
            check('background click clears selection',page.evaluate('window.sceneEditor.selected') is None)
            page.mouse.click(*model_origin)
            check('viewport click selects Gaussian model',page.evaluate('window.sceneEditor.selected')=='qa-room-a')
            page.locator('#scene-canvas').focus()
            page.keyboard.press('w')
            check('W selects translate',page.evaluate('window.sceneEditor.transformMode')=='translate')
            before = state(); camera_before = before['view']['position']
            drag('x',70,0)
            moved = selected()['position'][0]
            check('drag changes selected model X',abs(moved)>.05)
            check('drag leaves camera in place',math.dist(state()['view']['position'],camera_before)<1e-6)
            page.keyboard.press('Control+z')
            check('one Undo restores whole drag',abs(selected()['position'][0])<1e-6)
            page.keyboard.press('Control+Shift+z')
            check('Redo restores drag',abs(selected()['position'][0]-moved)<1e-6)
            before_cancel = selected()['position']
            drag('x',45,0,cancel=True)
            check('Escape cancels current drag',math.dist(selected()['position'],before_cancel)<1e-6)
            page.locator('#scene-canvas').focus();page.keyboard.press('Alt+g')
            check('Alt+G resets position',math.dist(selected()['position'],[0,0,0])<1e-6)
            page.keyboard.press('e')
            check('E selects rotation',page.evaluate('window.sceneEditor.transformMode')=='rotate')
            frames()
            ring=page.evaluate('window.sceneEditor.gizmoScreen')
            ring_point=[ring['origin'][i]+(ring['x'][i]-ring['origin'][i])*.5/.45 for i in range(2)]
            page.keyboard.press('y')
            check('Y constrains axis',page.evaluate('window.sceneEditor.axis')=='Y')
            page.mouse.move(*ring_point);page.mouse.down();page.mouse.move(ring_point[0]+35,ring_point[1]+25,steps=12)
            check('mouse drag engages rotation ring',page.evaluate('window.sceneEditor.dragging'))
            page.mouse.up();frames()
            check('Y ring rotates only model yaw',abs(selected()['rotation'][1])>.01 and abs(selected()['rotation'][0])<1e-6 and abs(selected()['rotation'][2])<1e-6)
            page.locator('#rotation-1').fill('45');page.locator('#rotation-1').press('Tab')
            check('rotation input changes actual scene',abs(selected()['rotation'][1]-math.pi/4)<1e-6)
            page.locator('#scene-canvas').focus();page.keyboard.press('Alt+r')
            check('Alt+R resets rotation',math.dist(selected()['rotation'],[0,0,0])<1e-6)
            page.keyboard.press('r')
            check('R selects scale',page.evaluate('window.sceneEditor.transformMode')=='scale')
            page.locator('#item-scale').fill('1.5');page.locator('#item-scale').press('Tab')
            page.locator('#scene-canvas').focus();page.keyboard.press('Alt+s')
            check('Alt+S resets scale',abs(selected()['scale']-1)<1e-6)
            page.locator('#position-2').fill('3');page.locator('#position-2').press('Tab')
            page.locator('#position-reset-2').click()
            check('per-axis reset restores Z',abs(selected()['position'][2])<1e-6)
            page.locator('#view-options > summary').click();page.locator('#reset-view').click();page.locator('#view-options > summary').click()
            check('Reset View restores saved camera',math.dist(state()['view']['position'],source['view']['position'])<1e-6)
            page.locator('#object-advanced > summary').click();page.locator('#auto-collision').click()
            check('automatic proxy adds one floor and four walls',len(state()['colliders'])==5)
            page.locator('#scene-undo').click()
            check('one Undo removes generated room',len(state()['colliders'])==0)
            page.locator('#scene-redo').click()
            check('Redo restores generated room',len(state()['colliders'])==5)
            with page.expect_response(lambda response:response.url==base+'/api/scenes/'+scene['id'] and response.request.method=='POST') as saved:
                page.locator('#scene-save').click()
            check('scene save succeeds',saved.value.status==200)
            page.reload();page.wait_for_function('window.sceneEditor?.loaded===2',timeout=30000);frames(30)
            check('saved proxies reopen',len(state()['colliders'])==5)
            page.locator('.object-select').filter(has_text='Room A (blue)').click()
            page.locator('#transform-translate').click()
            if args.full_workflow:
                from test_scene_workflow import run_workflow
                run_workflow(page,browser,base,out,state,frames,check,drag)
            page.screenshot(path=str(out/'scene-gizmo-desktop.png'),full_page=True)
            page.locator('#export-open').click()
            with page.expect_download() as downloaded:
                page.locator('#collision-download').click()
            downloaded.value.save_as(str(out/'collision.obj'))
            page.keyboard.press('Escape')
            check('OBJ contains proxy triangles',(out/'collision.obj').read_text().count('\nf ')==60)
            page.set_viewport_size({'width':390,'height':844});frames()
            check('mobile layout fits viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.screenshot(path=str(out/'scene-gizmo-mobile.png'),full_page=True)
            check('no page, console or HTTP errors',not report['page_errors'] and not report['console_errors'] and not report['http_errors'])
            report['passed']=True
        except Exception as error:
            report['passed']=False;report['failure']=str(error)
            report['status']=page.locator('#scene-status').inner_text()
            report['error_box']=page.locator('#scene-error').inner_text()
            report['state']=state()
            page.screenshot(path=str(out/'scene-gizmo-failure.png'),full_page=True)
            raise
        finally:
            (out/'chromium-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
            if args.keep_open and report.get('passed'):
                page.set_viewport_size({'width':1600,'height':1000})
                page.locator('#view-options > summary').click();page.locator('#show-grid').uncheck();page.locator('#show-colliders').uncheck();page.locator('#view-options > summary').click()
                page.locator('.object-select').filter(has_text='Room A (blue)').click()
                page.locator('.scene-panel').evaluate_all('(panels)=>panels.forEach(panel=>panel.scrollTop=0)')
                page.locator('#view-options > summary').click();page.locator('#reset-view').click();page.locator('#view-options > summary').click()
                page.bring_to_front();frames(40)
                print(json.dumps({'desktop_fits':page.evaluate('document.documentElement.scrollHeight<=innerHeight+2'),'view':state()['view']},ensure_ascii=False),flush=True)
                page.screenshot(path=str(out/'visible-chromium.png'),full_page=True)
                print(json.dumps({'ready':True,'checks':len(report['checks']),'url':url,'report':str(out/'chromium-report.json')},ensure_ascii=False),flush=True)
                page.wait_for_event('close',timeout=0)
            browser.close()
    print(json.dumps({'passed':True,'checks':len(report['checks']),'url':url,'report':str(out/'chromium-report.json')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
