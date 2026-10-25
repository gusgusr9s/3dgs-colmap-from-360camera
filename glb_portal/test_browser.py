"""Isolated Chromium workflow and responsive UI checks (requires Playwright)."""
import argparse
import json
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import server
from portal_test_fixtures import (gaussian_ply,raw_splat,mesh_glb,mesh_ply,obj_mesh,
                                  gltf_files,zip_bytes,scene_zip,png_bytes,legacy_spz)

ROOT = Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium',type=Path,default=ROOT/'tools/browser-qa/browsers/chromium-1248/chrome-win64/chrome.exe')
    parser.add_argument('--output',type=Path,default=ROOT/'tools/browser-qa/portal-review')
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--scene-glb',type=Path,help='Also verify a GLB exported by the scene editor')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report={'checks':[], 'errors':[], 'console_errors':[]}
    def check(name,condition):
        if not condition:raise AssertionError(name)
        report['checks'].append(name);print(name,flush=True)
    asset_gets=[]
    class QuietHandler(server.Handler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.command=='GET' and '/assets/' in self.path:asset_gets.append(self.path)
            super().do_GET()
    with tempfile.TemporaryDirectory(prefix='portal-browser-') as temporary:
        server.DATA=Path(temporary);server.DB=server.DATA/'portal.sqlite3';server.init_db()
        with server.connect() as db:
            for name,role in [('portal_test','admin'),('portal_guest','viewer')]:
                db.execute('INSERT INTO users VALUES(?,?,?,?)',(name,server.password_hash('local-test-password'),role,server.now()))
        http=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{http.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=str(args.chromium),headless=not args.headed)
                context=browser.new_context(viewport={'width':1440,'height':960})
                page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                page.on('console',lambda message:report['console_errors'].append(message.text) if message.type=='error' and not message.text.startswith('Failed to load resource:') else None)
                def capture(name):page.screenshot(path=str(args.output/(name+'.png')))
                def login(user='portal_test'):
                    page.goto(base,wait_until='networkidle');page.wait_for_function('() => document.documentElement.dataset.portalReady==="1"')
                    page.locator('[name=username]').fill(user);page.locator('[name=password]').fill('local-test-password')
                    page.locator('#login-form button').click();page.locator('#workspace').wait_for(state='visible')
                    page.wait_for_function('() => document.querySelector("#project-select").options.length>0')
                def choose(name):
                    page.locator('.open-model').filter(has=page.locator('strong',has_text=name)).click()
                    page.wait_for_function('() => document.querySelector("#reset-view").disabled===false',timeout=60000)
                    page.wait_for_timeout(400)
                try:
                    login();capture('after-empty-desktop')
                    check('less-used settings and uploads are in dialogs',not page.locator('#viewer-settings').is_visible() and not page.locator('#upload-panel').is_visible())
                    page.locator('#upload-open').click()
                    check('upload limit matches server configuration',page.locator('#upload-limit').inner_text()==f'สูงสุด {server.MAX_UPLOAD/1073741824:.1f} GB ต่อไฟล์')
                    files=[{'name':'Room A.glb','mimeType':'application/octet-stream','buffer':mesh_glb()},
                           {'name':'broken.ply','mimeType':'application/octet-stream','buffer':b'not a valid ply file' * 3},
                           {'name':'Room B.ply','mimeType':'application/octet-stream','buffer':gaussian_ply()},
                           {'name':'Room C.splat','mimeType':'application/octet-stream','buffer':raw_splat()},
                           {'name':'Room D.spz','mimeType':'application/octet-stream','buffer':(server.ROOT/'test_splat.spz').read_bytes()}]
                    page.locator('#model-file').set_input_files(files)
                    page.wait_for_function('() => document.querySelector("#upload-result").hidden===false',timeout=30000)
                    check('mixed upload queue saves all four formats and reports invalid file',page.locator('#upload-queue li[data-status=saved]').count()==4 and page.locator('#upload-queue li[data-status=error]').count()==1)
                    check('upload does not start the GPU viewer',page.locator('#canvas-host canvas').count()==0)
                    capture('after-upload');page.keyboard.press('Escape')
                    page.locator('#format-filter').select_option('ply')
                    check('format filter shows PLY only',page.locator('.open-model').count()==1 and 'Room B' in page.locator('.open-model').inner_text())
                    page.locator('#model-search').fill('missing')
                    check('search has a useful empty state',page.locator('#library-empty').is_visible() and 'ไม่พบ' in page.locator('#library-empty').inner_text())
                    page.locator('#model-search').fill('');page.locator('#format-filter').select_option('all')
                    page.locator('#model-sort').select_option('name')
                    check('name sorting works',page.locator('.open-model strong').all_text_contents()==['Room A','Room B','Room C','Room D'])
                    for name,format in [('Room A','GLB'),('Room B','PLY'),('Room C','SPLAT'),('Room D','SPZ')]:
                        choose(name)
                        check(f'{format} actually renders in Chromium',format in page.locator('#render-info').inner_text() and page.locator('#canvas-host canvas').count()==1)
                        if format in ('PLY','SPZ'):
                            page.wait_for_function('() => document.querySelector(".model-item.active .model-thumbnail")?.naturalWidth>0')
                            cover=page.locator('.model-item.active .model-thumbnail').get_attribute('src')
                            colors=page.evaluate('''async url => { const image=await createImageBitmap(await (await fetch(url)).blob());const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const context=canvas.getContext('2d');context.drawImage(image,0,0);const pixels=context.getImageData(0,0,image.width,image.height).data;const colors=new Set();for(let i=0;i<pixels.length;i+=16)colors.add(pixels[i]+','+pixels[i+1]+','+pixels[i+2]);return colors.size; }''',cover)
                            check(format+' thumbnail contains the rendered model',colors>5)
                        capture('render-'+format.lower())
                    page.locator('[data-mode=walk]').click()
                    check('walk mode works',page.locator('[data-mode=walk]').get_attribute('aria-pressed')=='true')
                    page.locator('#canvas-host canvas').focus();page.keyboard.down('w');page.wait_for_timeout(120)
                    for target in ('Room B','Room C','Room A','Room D'):
                        page.locator('.open-model').filter(has=page.locator('strong',has_text=target)).click()
                    page.keyboard.up('w');page.wait_for_function('() => document.querySelector("#reset-view").disabled===false')
                    check('rapid swaps leave one current viewer',page.locator('#canvas-host canvas').count()==1 and page.locator('#viewer-title').inner_text()=='Room D')
                    page.locator('[data-mode=free]').click()
                    check('free view works',page.locator('#touch-altitude').get_attribute('hidden') is None)
                    page.locator('[data-mode=orbit]').click();page.locator('#reset-view').click()
                    page.locator('#model-menu summary').click();check('download is grouped with model actions',page.locator('#download-model').is_visible())
                    page.locator('#viewer-orientation').click();check('splat orientation can be reset',page.locator('#viewer-orientation').text_content()=='หมุนกลับ 180°')
                    page.locator('#model-menu summary').click();page.locator('#edit-selected-name').click()
                    page.locator('#selected-name').fill('Renamed room');page.locator('#selected-name-form button[type=submit]').click()
                    page.wait_for_function('() => document.querySelector("#viewer-title").textContent==="Renamed room"')
                    check('rename updates both viewer and library',page.locator('.open-model strong',has_text='Renamed room').count()==1)
                    page.locator('#model-menu summary').click();page.keyboard.press('Escape')
                    check('Escape closes action menu and restores focus',not page.locator('#model-menu').evaluate('(element) => element.open') and page.locator('#model-menu summary').evaluate('(element) => element===document.activeElement'))
                    page.locator('#new-project').click();page.locator('#project-name').fill('Another place');page.locator('#project-form button[type=submit]').click()
                    page.wait_for_function('() => document.querySelector("#project-select").selectedOptions[0].textContent.includes("Another place")')
                    check('new project has its own empty library',page.locator('.open-model').count()==0)
                    page.locator('#upload-open').click()
                    def stop_after_current(route):
                        if route.request.method=='POST':page.locator('#upload-cancel').click()
                        route.continue_()
                    page.route('**/api/models',stop_after_current)
                    page.locator('#model-file').set_input_files([files[0],files[2]])
                    page.wait_for_function('() => document.querySelector("#upload-result").hidden===false')
                    page.unroute('**/api/models',stop_after_current)
                    check('stopping a queue saves current file and skips remaining files',page.locator('#upload-queue li[data-status=saved]').count()==1 and page.locator('#upload-queue li[data-status=cancelled]').count()==1)
                    page.keyboard.press('Escape');check('queue stays in the chosen project',page.locator('.open-model').count()==1)
                    page.once('dialog',lambda dialog:dialog.accept());page.locator('.delete-model').click()
                    page.wait_for_function('() => document.querySelectorAll(".open-model").length===0')
                    check('deleting the final model restores an empty project',page.locator('#library-empty').is_visible())
                    page.locator('#project-select').select_option('default');page.wait_for_function('() => document.querySelectorAll(".open-model").length===4')
                    check('switching project preserves original files',page.locator('.open-model').count()==4)
                    choose('Renamed room')
                    page.locator('#upload-open').click()
                    common=[('ASCII mesh.ply',mesh_ply()),('Big endian mesh.ply',mesh_ply('binary_big_endian')),
                            ('Point cloud.ply',mesh_ply('ascii',False)),('Plain mesh.obj',obj_mesh()),
                            ('Inline scene.gltf',gltf_files(True)['model/scene.gltf']),('Walking.glb',mesh_glb(True)),
                            ('Legacy v1.spz',legacy_spz(1)),('Legacy v2.spz',legacy_spz(2)),('Native cameras.glb',mesh_glb(native_camera=True))]
                    page.locator('#model-file').set_input_files([{'name':name,'mimeType':'application/octet-stream','buffer':data} for name,data in common])
                    page.wait_for_function('() => document.querySelector("#upload-result").hidden===false')
                    check('general meshes and point clouds upload without app metadata',page.locator('#upload-queue li[data-status=saved]').count()==len(common))
                    page.keyboard.press('Escape')
                    for name,expected in [('ASCII mesh','MESH PLY'),('Big endian mesh','MESH PLY'),('Point cloud','POINT CLOUD PLY'),('Plain mesh','MESH OBJ'),('Inline scene','MESH GLTF'),('Legacy v1','SPZ'),('Legacy v2','SPZ')]:
                        choose(name);check(name+' renders',expected in page.locator('#render-info').inner_text())
                    page.wait_for_function('() => document.querySelector(".model-item.active .model-thumbnail")?.naturalWidth>0')
                    check('cover images are saved once models have been viewed',page.locator('.model-thumbnail').count()>=5)
                    capture('standard-model-library')
                    choose('Native cameras');page.locator('#places-open').click()
                    check('standard glTF cameras appear as navigation views',page.locator('.place-go',has_text='Entrance camera').count()==1)
                    page.locator('.place-go',has_text='Entrance camera').click()
                    page.locator('#places-open').click();page.locator('#place-name').fill('Imported camera');page.locator('#place-form button').click()
                    page.locator('.place-go',has_text='Imported camera').wait_for(state='visible')
                    camera_model=page.url.split('model=')[1].split('&')[0]
                    imported_camera=page.request.get(base+'/api/models/'+camera_model+'/details').json()['viewpoints'][0]
                    check('glTF camera node transform is preserved',imported_camera['position']==[3,2,4])
                    page.keyboard.press('Escape')
                    def import_zip(name,data):
                        page.locator('#upload-open').click();page.locator('#model-file').set_input_files({'name':name+'.zip','mimeType':'application/zip','buffer':data})
                        page.locator('#import-dialog').wait_for(state='visible');capture('import-'+name.replace(' ','-'))
                        page.locator('#import-name').fill(name);page.locator('#import-commit').click()
                        page.locator('#import-dialog').wait_for(state='hidden');page.wait_for_function('() => document.querySelector("#upload-result").hidden===false')
                        check(name+' imports after preview',page.locator('#upload-queue li[data-status=saved]').count()==1)
                        page.keyboard.press('Escape');choose(name)
                    import_zip('Standard glTF',zip_bytes(gltf_files()))
                    standard_gltf_id=page.url.split('model=')[1].split('&')[0]
                    check('standard glTF ZIP loads its buffer and texture',page.locator('#render-info').inner_text()=='MESH SCENE ZIP')
                    capture('standard-gltf-zip')
                    import_zip('Standard OBJ',zip_bytes({'model.obj':obj_mesh(True),'material.mtl':b'newmtl painted\nKd 1 1 1\nmap_Kd textures/paint.png\n','textures/paint.png':png_bytes()}))
                    check('OBJ ZIP renders with MTL and texture',page.locator('#render-info').inner_text()=='MESH SCENE ZIP')
                    capture('standard-obj-zip')
                    before_fetches=sum('/'+standard_gltf_id+'/' in value for value in asset_gets)
                    choose('Standard glTF')
                    check('ZIP buffers and textures reuse authenticated model cache',sum('/'+standard_gltf_id+'/' in value for value in asset_gets)==before_fetches)
                    import_zip('Optional scene',scene_zip())
                    check('optional scene ZIP renders Gaussian assets and transforms','SPLATS' in page.locator('#render-info').inner_text())
                    choose('Walking');page.locator('[data-mode=walk]').click()
                    page.locator('#settings-open').click();page.locator('#movement-speed').select_option('2')
                    check('authored floor and wall are recognized','2 กล่อง' in page.locator('#collision-info').inner_text())
                    page.locator('#collision-helpers').check();page.keyboard.press('Escape');capture('walk-collision-helpers')
                    def save_view(label):
                        page.locator('#places-open').click();page.locator('#place-name').fill(label);page.locator('#place-form button').click()
                        page.locator('.place-go',has_text=label).wait_for(state='visible');page.keyboard.press('Escape')
                        identifier=page.url.split('model=')[1].split('&')[0]
                        data=page.request.get(base+'/api/models/'+identifier+'/details').json()
                        return next(point for point in data['viewpoints'] if point['name']==label)
                    before=save_view('Before walk')
                    check('walking starts at the authored spawn',before['position']==[0,1.65,1])
                    page.locator('#canvas-host canvas').focus();page.keyboard.down('w');page.wait_for_timeout(1600);page.keyboard.up('w')
                    after=save_view('After walk')
                    check('walk stops at the wall while standing on the floor',-.651<=after['position'][2]<.5 and abs(after['position'][1]-1.65)<.01)
                    page.locator('[data-mode=free]').click();page.locator('#canvas-host canvas').focus();page.keyboard.down('w');page.wait_for_timeout(700);page.keyboard.up('w')
                    flown=save_view('Free view')
                    check('free view can move through the same wall',flown['position'][2]<-.9)
                    page.locator('#places-open').click();page.locator('.place-go',has_text='Before walk').click()
                    returned=save_view('Returned')
                    check('a saved viewpoint restores position and mode',returned['position']==before['position'] and returned['mode']=='walk')
                    page.reload(wait_until='networkidle');page.locator('#places-open').click()
                    check('saved viewpoints survive reload',page.locator('.place-go',has_text='After walk').count()==1)
                    capture('saved-views');page.locator('.place-delete[aria-label="ลบจุด Free view"]').click()
                    page.locator('.place-go',has_text='Free view').wait_for(state='hidden');check('saved viewpoints can be removed',page.locator('.place-go',has_text='Free view').count()==0)
                    page.keyboard.press('Escape')
                    page.locator('#project-manage').click();page.locator('#project-rename-name').fill('Shared models');page.locator('#project-rename-form button').click()
                    page.locator('#project-dialog').wait_for(state='hidden');check('Project can be renamed','Shared models' in page.locator('#project-select').inner_text())
                    destination=page.locator('#project-select option',has_text='Another place').get_attribute('value')
                    page.locator('#selection-toggle').click();page.get_by_role('checkbox',name='เลือก Plain mesh',exact=True).check();page.get_by_role('checkbox',name='เลือก Point cloud',exact=True).check()
                    page.locator('#bulk-open').click();page.locator('#bulk-project').select_option(destination);page.locator('#bulk-move').click();page.locator('#bulk-dialog').wait_for(state='hidden')
                    page.locator('#project-select').select_option(destination);page.wait_for_function('() => document.querySelectorAll(".open-model").length===2')
                    check('multiple models move to the chosen Project',page.locator('.open-model').count()==2)
                    page.locator('#selection-toggle').click();page.locator('#select-visible').click();page.locator('#bulk-open').click();page.once('dialog',lambda dialog:dialog.accept());page.locator('#bulk-delete').click()
                    page.locator('#bulk-dialog').wait_for(state='hidden');page.wait_for_function('() => document.querySelectorAll(".open-model").length===0')
                    check('multiple models can be deleted together',page.locator('#library-empty').is_visible())
                    page.locator('#project-select').select_option('default');page.wait_for_function('() => document.querySelectorAll(".open-model").length>0')
                    if args.scene_glb:
                        page.locator('#upload-open').click();page.locator('#model-file').set_input_files(str(args.scene_glb.resolve()))
                        page.wait_for_function('() => document.querySelector("#upload-result").hidden===false')
                        check('scene editor GLB uploads successfully',page.locator('#upload-queue li[data-status=saved]').count()==1)
                        page.keyboard.press('Escape');choose(args.scene_glb.stem)
                        check('combined Gaussian scene GLB renders', 'SPLATS' in page.locator('#render-info').inner_text())
                        capture('scene-glb')
                    for width,height in [(1440,960),(1024,768),(768,900),(390,844),(320,740),(844,390)]:
                        page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(150)
                        check(f'{width}px page has no horizontal overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        capture(f'layout-{width}')
                        page.locator('#settings-open').click()
                        check(f'{width}px settings fit on screen',page.locator('#settings-dialog').bounding_box()['width']<=width)
                        capture(f'settings-{width}');page.keyboard.press('Escape')
                    page.set_viewport_size({'width':390,'height':844})
                    page.locator('#library-toggle').click()
                    check('mobile library opens as a drawer',page.locator('#library').get_attribute('aria-modal')=='true')
                    page.locator('#library-close').focus();page.keyboard.press('Shift+Tab')
                    check('mobile drawer keeps keyboard focus inside',page.locator('#library').evaluate('(element) => element.contains(document.activeElement)'))
                    capture('mobile-library')
                    page.locator('#model-search').fill('Room A');page.locator('.open-model').click()
                    check('selecting a model closes the mobile drawer',not page.locator('#library').is_visible())
                    page.wait_for_function('() => document.querySelector("#reset-view").disabled===false')
                    page.locator('#settings-open').click();page.locator('#movement-speed').select_option('1');page.keyboard.press('Escape')
                    page.reload(wait_until='networkidle');page.locator('#settings-open').click()
                    check('viewer preferences survive reload',page.locator('#movement-speed').input_value()=='1')
                    page.keyboard.press('Escape');page.locator('#account-menu summary').click();page.locator('#logout').click()
                    page.locator('#login-screen').wait_for(state='visible');login('portal_guest')
                    page.locator('#library-toggle').click()
                    check('viewer role has no upload, rename, delete or project creation controls',not page.locator('#upload-open').is_visible() and not page.locator('#new-project').is_visible() and page.locator('.rename-model,.delete-model').count()==0)
                    page.keyboard.press('Escape')
                    check('no JavaScript exceptions or renderer errors',not report['errors'] and not report['console_errors'])
                    report['passed']=True
                except Exception:
                    capture('failure');raise
                finally:
                    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
                    browser.close()
        finally:http.shutdown();http.server_close();thread.join()
    print(f"Passed {len(report['checks'])} checks; screenshots: {args.output}")


if __name__=='__main__':main()
