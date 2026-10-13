"""Create private repo and store authorized bot/Drive credentials as Actions secrets."""
import base64
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def main():
    from nacl.public import PublicKey, SealedBox
    proc = subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,check=True)
    token = dict(x.split('=',1) for x in proc.stdout.splitlines() if '=' in x)['password']
    def api(method,path,body=None):
        req = urllib.request.Request('https://api.github.com'+path,
            data=json.dumps(body).encode() if body is not None else None,method=method,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=40) as r:
                data=r.read(); return json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            if method=='GET' and e.code==404: return None
            raise SystemExit(f'GitHub HTTP {e.code}') from None
    repo='ncd03-nb/telegram-payload-bot'
    obj=api('GET','/repos/'+repo)
    if obj is None:
        obj=api('POST','/user/repos',{'name':'telegram-payload-bot','private':True,'description':'Telegram bot: remote OTA payload partition extraction with verified Google Drive uploads'})
    if obj.get('full_name')!=repo or not obj.get('private'):
        raise SystemExit('Unexpected repository; refusing secret configuration.')
    env_path=Path('.env'); lines=env_path.read_text(encoding='utf-8-sig').splitlines()
    env=dict(x.split('=',1) for x in lines if '=' in x and not x.startswith('#'))
    secrets={'TELEGRAM_BOT_TOKEN':env['TELEGRAM_BOT_TOKEN'].strip().strip('\"\''),
        'ALLOWED_USER_IDS':env['ALLOWED_USER_IDS'],
        'GOOGLE_TOKEN_B64':base64.b64encode(Path('config/google-token.json').read_bytes()).decode()}
    if not secrets['TELEGRAM_BOT_TOKEN']: raise SystemExit('Missing new bot token')
    key=api('GET','/repos/'+repo+'/actions/secrets/public-key')
    box=SealedBox(PublicKey(base64.b64decode(key['key'])))
    for name,value in secrets.items():
        api('PUT','/repos/'+repo+'/actions/secrets/'+name,{'key_id':key['key_id'],'encrypted_value':base64.b64encode(box.encrypt(value.encode())).decode()})
        print('Configured secret:',name)
    variables={name:('true' if env.get(name,'false').lower() in {'true','1','yes'} else 'false')
               for name in ('PUBLIC_BOT','ALLOW_GROUPS','PUBLIC_FILES')}
    for name,default,limit in (('EXTRACT_WORKERS',8,16),('RANGE_BATCH_MIB',8,32),('DECODE_WORKERS',2,4),('DRIVE_CHUNK_MIB',32,256)):
        value=int(env.get(name,str(default)))
        if not 1 <= value <= limit: raise SystemExit('Invalid '+name)
        variables[name]=str(value)
    for name,value in variables.items():
        path='/repos/'+repo+'/actions/variables'
        exists=api('GET',path+'/'+name) is not None
        api('PATCH' if exists else 'POST',path+'/'+name if exists else path,{'name':name,'value':value})
        print('Configured variable:',name)
    values={'ACTIONS_DISPATCH_TOKEN':token,'ACTIONS_REPO':repo,'ACTIONS_REF':'main'}
    lines=[x for x in lines if x.split('=',1)[0] not in values]
    env_path.write_text('\n'.join(lines)+'\n'+'\n'.join(k+'='+v for k,v in values.items())+'\n',encoding='utf-8')
    print('Private repository ready:',obj['html_url'])


if __name__=='__main__': main()
