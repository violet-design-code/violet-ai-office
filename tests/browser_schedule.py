"""Real form operations plus explicitly mocked browser voice APIs; no real microphone/cloud."""
import argparse,json,threading,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
from tests import test_schedule as helpers
from violet_office.web import make_server

VOICE_MOCK='''
window.__voiceMock=true;window.__spoken=[];
class Recognition {
  static async available(options) {window.__voiceOptions=options;return 'available';}
  get processLocally(){return true;} set processLocally(v){window.__localFlag=v;}
  start(){setTimeout(()=>{this.onresult({results:[[{transcript:'11月5日の14時から石川さんと佐久市で打ち合わせするから予定入れて'}]]});this.onend();},10);}
  stop(){this.onend();} abort(){}
}
window.SpeechRecognition=Recognition;
Object.defineProperty(window,'speechSynthesis',{value:{getVoices:()=>[{lang:'ja-JP',localService:true}],addEventListener:()=>{},cancel:()=>{},speak:(u)=>{window.__spoken.push(u.text);}}});
window.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};
'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',required=True);args=parser.parse_args();out=Path(args.output_dir);out.mkdir(parents=True,exist_ok=True);results=[];fixture=helpers.ScheduleTests();fixture.setUp();server=make_server(fixture.path,0);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            for width in (1440,390):
                context=browser.new_context(viewport={'width':width,'height':1000},locale='ja-JP',timezone_id='Asia/Tokyo');context.add_init_script(VOICE_MOCK);page=context.new_page();page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/login');page.get_by_label('ユーザー名',exact=True).fill('owner');page.get_by_label('パスワード',exact=True).fill('SyntheticOwnerPass!123');page.get_by_role('button',name='ログイン',exact=True).click();page.goto(base+'/schedule')
                def screen(name):
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name
                    page.screenshot(path=str(out/f'{width}-{name}.png'),full_page=True);results.append({'width':width,'check':name,'state':'passed'})
                page.get_by_role('button',name='端末内で音声入力',exact=True).click();page.wait_for_function("document.getElementById('schedule-instruction').value.includes('佐久市')");assert page.evaluate('window.__localFlag') is True;assert page.evaluate('window.__voiceOptions.processLocally') is True;page.get_by_role('button',name='指示を確認・検索する（まだ登録しない）').click();assert 'まだ登録していません' in page.locator('#schedule-answer').inner_text();screen('voice-candidate-mocked')
                # Dates differ per viewport to avoid unintended collisions in the shared synthetic DB.
                day='2026-11-05' if width==1440 else '2026-11-06';f=page.locator('form').filter(has=page.locator('input[name="action"][value="save"]'));f.locator('[name="title"]').fill('合成の音声候補'+str(width));f.locator('[name="start"]').fill(day+'T14:00');f.locator('[name="end"]').fill(day+'T15:00');f.locator('[name="checked"]').check();f.get_by_role('button').click();assert '保存しました' in page.locator('#schedule-answer').inner_text();page.get_by_role('button',name='回答を端末内で読み上げ').click();assert '保存しました' in page.evaluate('window.__spoken[0]');screen('saved-and-spoken-mocked')
                # Actual browser keyboard navigation and explicit edit confirmation.
                page.get_by_role('link',name='変更・キャンセル').first.click();f=page.locator('form').filter(has=page.locator('input[name="action"][value="save"]'));f.locator('[name="title"]').fill('合成の変更後'+str(width));f.locator('[name="checked"]').check();f.get_by_role('button').click();assert '変更後' in page.locator('#schedule-answer').inner_text();screen('updated')
                f=page.locator('form').filter(has=page.locator('input[name="action"][value="settings"]'));f.locator('[name="enabled"]').check();f.locator('[name="consent"]').check();f.locator('[name="checked"]').check();f.get_by_role('button').click();assert '設定を保存' in page.locator('#schedule-answer').inner_text();screen('settings-mock-only')
                page.goto(base+'/schedule?date='+day);page.get_by_role('link',name='変更・キャンセル').first.click();f=page.locator('form').filter(has=page.locator('input[name="action"][value="cancel"]'));f.locator('[name="checked"]').check();f.get_by_role('button').click();assert 'キャンセルしました' in page.locator('#schedule-answer').inner_text();screen('cancelled')
                assert not errors,errors;context.close()
            # Exercise the actual installed Chromium capabilities, without pretending microphones exist.
            context=browser.new_context(viewport={'width':390,'height':900},locale='ja-JP');page=context.new_page();page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(base+'/') else r.abort());page.goto(base+'/login');page.locator('[name="username"]').fill('owner');page.locator('[name="password"]').fill('SyntheticOwnerPass!123');page.get_by_role('button',name='ログイン',exact=True).click();page.goto(base+'/schedule');page.wait_for_function("!document.getElementById('voice-status').textContent.includes('確認中')");actual_status=page.locator('#voice-status').inner_text();assert not page.evaluate('window.__voiceMock || false');context.close();browser.close()
        report={'mode':'synthetic_temporary_database','checks':results,'voice_api_logic':'explicit_browser_mock','actual_browser_voice_status':actual_status,'actual_microphone_audio':'not_tested','actual_audio_output':'not_tested','real_iphone':'not_tested','actual_external_notifications':False};(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':len(results),'failed':0,'voice_mock':True,'real_iphone':False}))
    finally:server.shutdown();server.server_close();fixture.tearDown()
if __name__=='__main__':main()
