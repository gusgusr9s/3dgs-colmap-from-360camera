"""Chromium usability checks and screenshots for the scene workspace."""
import argparse
import json
from pathlib import Path
from urllib.request import Request,urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/'tools/browser-qa'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,default=QA/'scene-full-test-workspace/fixture.json')
    args=parser.parse_args();fixture=json.loads(args.fixture.read_text(encoding='utf8'));workspace=Path(fixture['workspace']).resolve()
    if not workspace.is_relative_to(QA.resolve()) or not (workspace/'.scene-test-fixture.json').is_file():raise ValueError('Use an isolated scene-test workspace')
    base=fixture['url'].split('/scene/')[0];source=json.load(urlopen(fixture['url'].replace('/scene/','/api/scenes/')))
    source['name']='Scene workspace · UI review';scene=json.load(urlopen(Request(base+'/api/scenes',data=json.dumps(source).encode(),headers={'Content-Type':'application/json'})))
    out=QA/'scene-ui-review';out.mkdir(exist_ok=True);report={'url':base+'/scene/'+scene['id'],'checks':[],'errors':[]}
    def check(name,condition):
        if not condition:raise AssertionError(name)
        report['checks'].append(name);print(name,flush=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(QA/'browsers/chromium-1248/chrome-win64/chrome.exe'),headless=True)
        page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda error:report['errors'].append(str(error)))
        def capture(name):page.screenshot(path=str(out/(name+'.png')))
        def state():return page.evaluate('window.sceneEditor.snapshot()')
        def object_row():return page.locator('.object-row').filter(has=page.locator('.object-select').filter(has_text='Room A (blue)'))
        try:
            page.goto(report['url']);page.wait_for_function('window.sceneEditor?.loaded===2',timeout=30000)
            page.locator('.object-select').filter(has_text='Room A (blue)').click();page.locator('#fit-all').click()
            check('common tools stay on one toolbar row',page.locator('.scene-toolbar').bounding_box()['height']<65)
            check('header shows save and one export entry',page.locator('#scene-save').is_visible() and page.locator('#export-open').is_visible() and not page.locator('#glb-start').is_visible())
            check('library opens only when adding models',not page.locator('#asset-search').is_visible())
            check('duplicate nudge controls are removed',page.locator('[data-nudge]').count()==0)
            check('icons keep accessible labels',page.locator('#scene-undo svg[aria-hidden="true"]').count()==1 and page.locator('#scene-undo').get_attribute('aria-label')=='ย้อนกลับ')
            capture('iteration-2-desktop')
            page.locator('#tab-object').focus();page.keyboard.press('ArrowRight')
            check('arrow keys switch inspector tabs',page.locator('#tab-align').get_attribute('aria-selected')=='true' and page.locator('#align-target').is_visible())
            before=state()['view'];page.keyboard.press('Home')
            check('tab Home selects first tab without resetting camera',page.locator('#tab-object').get_attribute('aria-selected')=='true' and state()['view']==before)
            page.locator('#transform-rotate').click();page.locator('#export-open').click();page.keyboard.press('w')
            check('modal prevents background transform shortcuts',page.evaluate('window.sceneEditor.transformMode')=='rotate')
            check('export formats have distinct descriptions',page.locator('#export-dialog .export-card').count()==3 and not page.locator('#scene-download').is_visible())
            capture('iteration-2-export');page.keyboard.press('Escape')
            check('Escape closes export and restores focus',page.locator('#export-dialog').get_attribute('open') is None and page.locator('#export-open').evaluate('(node)=>node===document.activeElement'))
            page.locator('#library-open').click()
            check('library focuses search on open',page.locator('#asset-search').evaluate('(node)=>node===document.activeElement'))
            page.locator('#asset-search').fill('qa-room-a');check('search filters library',page.locator('.asset-row').count()==1)
            page.locator('.asset-row input').check();check('add action reports selection count','1' in page.locator('#assets-add').inner_text())
            capture('iteration-2-library');page.locator('#assets-add').click();page.wait_for_function('window.sceneEditor.loaded===3')
            check('adding a model closes library and selects the model',page.locator('#library-dialog').get_attribute('open') is None and len(state()['objects'])==3)
            page.locator('#scene-undo').click();page.wait_for_function('window.sceneEditor.loaded===2')
            page.locator('.object-select').filter(has_text='Room A (blue)').click();object_row().locator('.object-visibility').click()
            page.wait_for_function('window.sceneEditor.loaded===1')
            check('hide updates icon and releases model',object_row().locator('[data-name="eyeOff"]').count()==1)
            object_row().locator('.object-visibility').click();page.wait_for_function('window.sceneEditor.loaded===2')
            page.locator('#precision-options > summary').click();page.locator('#snap-enabled').check()
            check('Snap state is visible on collapsed menu label','Snap' in page.locator('#precision-label').inner_text())
            selected=page.evaluate('window.sceneEditor.selected');page.keyboard.press('Escape')
            check('Escape dismisses precision menu and retains selection',page.locator('#precision-options').get_attribute('open') is None and page.evaluate('window.sceneEditor.selected')==selected)
            page.locator('#reset-options > summary').click();check('reset menu fits within inspector',page.locator('#reset-options .menu-surface').bounding_box()['y']>=66);capture('iteration-2-reset');page.keyboard.press('Escape')
            page.locator('#tab-align').click();capture('iteration-2-alignment');page.locator('#tab-walk').click();capture('iteration-2-walk-settings');page.locator('#tab-object').click()
            page.locator('#transform-translate').click();page.locator('#fit-selected').click()
            for width,height in [(1280,900),(1024,768),(768,1024),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                check(f'{width}px layout stays inside viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight'))
                check(f'{width}px keeps canvas usable',page.locator('#scene-canvas').bounding_box()['height']>350)
                capture(f'iteration-2-{width}')
            check('mobile hides panels until requested',not page.locator('.library-panel').is_visible() and not page.locator('.inspector-panel').is_visible())
            page.locator('#objects-toggle').click();check('mobile opens scene list',page.locator('.library-panel').is_visible());capture('iteration-2-mobile-objects');page.keyboard.press('Escape')
            check('mobile drawer closes with Escape',page.locator('#objects-toggle').get_attribute('aria-expanded')=='false' and not page.locator('.library-panel').is_visible())
            page.locator('#inspector-toggle').click();page.locator('#tab-align').click();page.locator('#calibration-a').click()
            check('point picking closes mobile drawer to expose canvas',not page.locator('.inspector-panel').is_visible())
            screen=page.evaluate('window.sceneEditor.projectPoint("qa-room-a",[0,0,0])');page.mouse.click(*screen)
            page.wait_for_function('window.sceneEditor.measurement[0]')
            check('picking a point returns to mobile alignment panel',page.locator('.inspector-panel').is_visible() and page.locator('#tab-align').get_attribute('aria-selected')=='true');capture('iteration-2-mobile-align');page.keyboard.press('Escape')
            page.locator('.mobile-dock [data-dialog-open="export-dialog"]').click()
            check('mobile export remains inside screen',page.locator('#export-dialog').bounding_box()['width']<=390);capture('iteration-2-mobile-export');page.keyboard.press('Escape')
            check('no browser errors during UI workflows',not report['errors']);report['passed']=True
        except Exception as error:
            report.update(passed=False,failure=str(error))
            try:capture('layout-failure')
            except Exception:pass
            raise
        finally:
            (out/'layout-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');browser.close()
    print(json.dumps({'passed':True,'checks':len(report['checks']),'report':str(out/'layout-report.json')}),flush=True)


if __name__=='__main__':main()
