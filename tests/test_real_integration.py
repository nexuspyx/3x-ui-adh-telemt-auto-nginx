"""Offline loopback integration with authenticated official binaries, never host services."""
import base64
import copy
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import socketserver
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, quote, urlsplit

from test_personal_xhttp import ROOT, FIXTURE, render, seed, SHARED, MAIN

BASELINE = 'v3.9.0'
XRAY_VERSION = '26.9.30'
XUI_SHA256 = 'd7cbe0bf6358ee0d2117c24fd2efb483502e411d38e2ea59bd0bf5e7a3e39390'
MIHOMO_VERSION = 'v1.19.32'
MIHOMO_SHA256 = 'ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20'


def port(allocated, udp=False):
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            number = sock.getsockname()[1]
        if number not in allocated:
            allocated.add(number)
            return number
    raise RuntimeError('Could not allocate a distinct fixture port')


def download(url, target):
    subprocess.run(['curl','-fLsS','--retry','3','--connect-timeout','15','--max-time','180',
                    url,'-o',str(target)], check=True, timeout=600)


def stop(process):
    if getattr(process, '_integration_stopped', False):
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # Also reap any descendant left after the parent exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)
    process._integration_stopped = True


class RealIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() == 0:
            raise RuntimeError('Real integration requires non-root')
        cls.temp = tempfile.TemporaryDirectory(prefix='real-tools-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.tools = Path(cls.temp.name)
        cached = os.environ.get('INTEGRATION_ARTIFACTS')
        archive, sidecar, mihomo = [cls.tools/n for n in ('x-ui.tar.gz','x-ui.sha256','mihomo.gz')]
        urls = (
            f'https://github.com/MHSanaei/3x-ui/releases/download/{BASELINE}/x-ui-linux-amd64.tar.gz',
            f'https://github.com/MHSanaei/3x-ui/releases/download/{BASELINE}/x-ui-linux-amd64.tar.gz.sha256',
            f'https://github.com/MetaCubeX/mihomo/releases/download/{MIHOMO_VERSION}/mihomo-linux-amd64-compatible-{MIHOMO_VERSION}.gz')
        for target, url in zip((archive,sidecar,mihomo),urls):
            if cached:
                shutil.copyfile(Path(cached)/target.name, target)
            else:
                download(url, target)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != XUI_SHA256:
            raise ValueError('Pinned 3x-ui archive checksum mismatch')
        if not re.fullmatch(XUI_SHA256+r'\s+\*?x-ui-linux-amd64.tar.gz\s*', sidecar.read_text()):
            raise ValueError('Upstream release checksum mismatch')
        if hashlib.sha256(mihomo.read_bytes()).hexdigest() != MIHOMO_SHA256:
            raise ValueError('Pinned Mihomo checksum mismatch')
        if os.uname().machine != 'x86_64':
            raise RuntimeError('Pinned integration artifacts require linux amd64')
        # Extract only the verified executable pair; runtime state is always private.
        with tarfile.open(archive) as bundle:
            for member in ('x-ui/x-ui','x-ui/bin/xray-linux-amd64'):
                entry = bundle.getmember(member)
                if not entry.isfile():
                    raise ValueError('Expected regular release binary')
                target = cls.tools/Path(member).name
                target.write_bytes(bundle.extractfile(entry).read()); target.chmod(0o755)
        cls.xui, cls.xray = cls.tools/'x-ui', cls.tools/'xray-linux-amd64'
        cls.mihomo = cls.tools/'mihomo'
        cls.mihomo.write_bytes(gzip.decompress(mihomo.read_bytes())); cls.mihomo.chmod(0o755)
        cls.nginx = os.environ.get('NGINX_BIN') or shutil.which('nginx')
        if not cls.nginx:
            raise RuntimeError('Real nginx is required')
        xray_version = subprocess.check_output([cls.xray,'version'], text=True)
        if not xray_version.startswith('Xray '+XRAY_VERSION+' '):
            raise ValueError('Unexpected bundled Xray version')
        print('\n3x-ui baseline:',BASELINE, '\n'+xray_version.splitlines()[0])
        print(subprocess.check_output([cls.mihomo,'-v'],text=True).strip())
        print(subprocess.run([cls.nginx,'-v'],capture_output=True,text=True,check=True).stderr.strip(),flush=True)
        cls.passed = []

    @classmethod
    def tearDownClass(cls):
        print('\nREAL XRAY INTEGRATION\n'+'\n'.join(f'{name:24} PASS' for name in cls.passed),flush=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='real-core-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.uds = self.root/'xhttp.sock'
        self.marker = 'FREE-INTEGRATION-'+os.urandom(12).hex()
        marker = self.marker.encode()
        self.origin_requests = []
        self.origin_responses = {}
        requests = self.origin_requests
        responses = self.origin_responses
        class Origin(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200); self.send_header('Content-Length',str(len(marker)))
                self.end_headers(); self.wfile.write(marker)
            def log_message(self,*args): pass
        class FragmentedOrigin(socketserver.BaseRequestHandler):
            def handle(handler):
                handler.request.settimeout(15)
                request = b''
                while b'\r\n\r\n' not in request:
                    chunk = handler.request.recv(4096)
                    if not chunk: return
                    request += chunk
                    if len(request) > 65536: raise ValueError('Oversized origin request')
                nonce = request.split(b' ',2)[1].decode('ascii')
                requests.append(nonce)
                headers = (f'HTTP/1.1 200 OK\r\nX-Forensic-Nonce: {nonce}\r\n'
                           f'Content-Length: {len(marker)}\r\nConnection: close\r\n\r\n').encode()
                responses[nonce] = headers + marker
                # Deliberately separate header fragments and body. No delay or retry.
                for chunk in (headers[:5], headers[5:23], headers[23:], marker):
                    handler.request.sendall(chunk)
        origin_type = socketserver.ThreadingTCPServer if ('grpc_fragmented' in self._testMethodName or 'mihomo' in self._testMethodName) else ThreadingHTTPServer
        self.origin = origin_type(('127.0.0.1',0),FragmentedOrigin if origin_type is socketserver.ThreadingTCPServer else Origin)
        if origin_type is socketserver.ThreadingTCPServer:
            self.origin.server_port = self.origin.server_address[1]
        self.addCleanup(self.origin.server_close)
        self.allocated_ports = {self.origin.server_port}
        self.public = port(self.allocated_ports)
        self.hysteria_port = port(self.allocated_ports, udp=True)
        self.backends = {k:port(self.allocated_ports) for k in ('reality','ws','trojan-grpc','tls','cover','panel','sub','api')}
        threading.Thread(target=self.origin.serve_forever,daemon=True).start()
        self.addCleanup(self.origin.shutdown)
        self.cert,self.key = self.root/'cert.pem',self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                        '-subj','/CN=deploy.example','-addext','subjectAltName=DNS:deploy.example,DNS:cover.example',
                        '-keyout',str(self.key),'-out',str(self.cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        pair = subprocess.check_output([self.xray,'x25519'], text=True,timeout=10)
        self.private = re.search(r'^PrivateKey: (.+)$',pair,re.M)[1]
        self.public_key = re.search(r'^Password \(PublicKey\): (.+)$',pair,re.M)[1]
        self.uuid = subprocess.check_output([self.xray,'uuid'],text=True,timeout=10).strip()
        self.auth = os.urandom(16).hex()
        self.rows,self.hosts = seed(private_key=self.private,public_key=self.public_key)
        # Existing render helper uses a..h placeholders; production uses openssl rand -hex 8.
        self.short_ids = [os.urandom(8).hex() for _ in range(8)]
        reality = json.loads(self.rows['reality']['stream_settings'])
        self.assertEqual(reality['realitySettings']['shortIds'], list('abcdefgh'))
        reality['realitySettings']['shortIds'] = self.short_ids
        self.rows['reality']['stream_settings'] = json.dumps(reality)
        self.build_subscriptions()

    def launch(self, argv, label, env=None):
        log = open(self.root/(label+'.log'),'wb')
        self.addCleanup(log.close)
        process = subprocess.Popen(list(map(str,argv)),cwd=self.root,env=env,stdout=log,stderr=log,start_new_session=True)
        self.addCleanup(stop,process)
        return process

    def ready(self, process, address=None, path=None, udp_port=None):
        deadline = time.monotonic()+15
        description = f'pid={process.pid}, TCP={address}, UDS={path}, UDP={udp_port}'
        udp_state = ''
        while time.monotonic()<deadline:
            self.assertIsNone(process.poll(), 'component exited before readiness: '+description)
            if path and path.is_socket(): return
            if udp_port:
                result = subprocess.run(['ss','-H','-lunp','sport = :'+str(udp_port)],
                                        capture_output=True,text=True,check=True,timeout=2)
                udp_state = result.stdout
                # Linux socket table plus owning PID proves a bound listener, not UDP connect().
                if any(len(fields := line.split()) >= 5 and fields[3] == f'127.0.0.1:{udp_port}'
                       and f'pid={process.pid},' in line for line in udp_state.splitlines()):
                    return
            if address:
                try:
                    with socket.create_connection(address,timeout=0.2): return
                except OSError: pass
            time.sleep(0.05)
        self.fail('component readiness deadline expired: '+description+'; ss: '+udp_state)

    def validate(self, config, name):
        path = self.root/(name+'.json'); path.write_text(json.dumps(config))
        result = subprocess.run([self.xray,'run','-test','-c',path],capture_output=True,text=True,timeout=20)
        # Do not dump configs/private credentials into CI logs.
        self.assertEqual(result.returncode,0, name+' rejected: '+(result.stdout+result.stderr)[-2000:])
        return path

    def build_subscriptions(self):
        """Real bundled x-ui, native private state overrides, actual installer SQL."""
        dbdir,bindir,logs = [self.root/n for n in ('db','bin','logs')]
        for directory in (dbdir,bindir,logs): directory.mkdir()
        shutil.copyfile(self.xray,bindir/self.xray.name); (bindir/self.xray.name).chmod(0o755)
        env = {**os.environ,'XUI_DB_FOLDER':str(dbdir),'XUI_BIN_FOLDER':str(bindir),'XUI_LOG_FOLDER':str(logs)}
        subprocess.run([self.xui,'setting','-username','integration','-password',self.auth,
                        '-port',str(self.backends['panel']),'-webBasePath','/panel/'],env=env,
                       check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        with sqlite3.connect(dbdir/'x-ui.db') as db:
            sql = render('sqlite3 $XUIDB',private_key=self.private,public_key=self.public_key)
            for placeholder, short_id in zip('abcdefgh', self.short_ids):
                sql = sql.replace('\"'+placeholder+'\"', '\"'+short_id+'\"')
            db.executescript(sql)
            db.execute('INSERT INTO clients(email,sub_id,uuid,password,auth,flow,enable) VALUES(?,?,?,?,?,?,1)',
                       ('integration','fixture',self.uuid,self.auth,self.auth,'xtls-rprx-vision'))
            cid = db.execute('SELECT id FROM clients WHERE email=?',('integration',)).fetchone()[0]
            for row in db.execute('SELECT id,remark,listen,port,stream_settings FROM inbounds').fetchall():
                iid,remark,listen,number,stream_json = row
                name = remark.split()[-1]
                stream = json.loads(stream_json)
                self.assertEqual(json.loads(self.rows[name]['settings'])['clients'],[])
                db.execute('INSERT INTO client_inbounds(client_id,inbound_id,flow_override) VALUES(?,?,?)',
                           (cid,iid,'xtls-rprx-vision' if name=='reality' else ''))
                if name=='xhttp':
                    self.assertEqual(listen,'/dev/shm/uds2023.sock,0666')
                    listen = str(self.uds)+',0666'
                elif name=='hysteria2':
                    self.assertEqual((listen,number),('',443))
                    listen='127.0.0.1'; number=self.hysteria_port
                    self.assertEqual(stream['tlsSettings']['serverName'],'deploy.example')
                    self.assertEqual(stream['tlsSettings']['alpn'],['h3'])
                    self.assertEqual(stream['tlsSettings']['certificates'][0]['certificateFile'], '/root/cert/deploy.example/fullchain.pem')
                    self.assertEqual(stream['tlsSettings']['certificates'][0]['keyFile'], '/root/cert/deploy.example/privkey.pem')
                    stream['tlsSettings']['certificates'][0].update(certificateFile=str(self.cert),keyFile=str(self.key))
                else:
                    self.assertEqual(listen,'127.0.0.1'); number=self.backends[name]
                if name=='reality':
                    self.assertEqual(stream['realitySettings']['target'],'127.0.0.1:9443')
                    stream['realitySettings']['target']=f"127.0.0.1:{self.backends['cover']}"
                db.execute('UPDATE inbounds SET listen=?,port=?,stream_settings=?,enable=1 WHERE id=?',
                           (listen,number,json.dumps(stream),iid))
            updates = {'webListen':'127.0.0.1','subListen':'127.0.0.1','subPort':str(self.backends['sub']),
                       'webCertFile':str(self.cert),'webKeyFile':str(self.key),
                       'subCertFile':str(self.cert),'subKeyFile':str(self.key),
                       'xrayTemplateConfig':json.dumps({'log':{'loglevel':'warning'},'inbounds':[],
                            'outbounds':[{'protocol':'freedom','tag':'direct','settings':{'finalRules':[
                                # Xray defaults block private targets. Only this test origin is allowed.
                                {'action':'allow','ip':['127.0.0.1'],'port':str(self.origin.server_port)}]}}],'routing':{'rules':[]}})}
            for key,value in updates.items():
                db.execute('DELETE FROM settings WHERE key=?',(key,))
                db.execute('INSERT INTO settings(key,value) VALUES(?,?)',(key,value))
        process = self.launch([self.xui],'subscription-server',env=env)
        self.ready(process,('127.0.0.1',self.backends['sub']))
        def get(path):
            return subprocess.check_output(['curl','-fsS','--noproxy','*','--cacert',str(self.cert),
                       '--resolve',f"deploy.example:{self.backends['sub']}:127.0.0.1",'--max-time','15',
                       f"https://deploy.example:{self.backends['sub']}/{path}/fixture"],timeout=20)
        self.raw = get('subscription')
        self.json_subscription = json.loads(get('jsonsub'))
        self.provider = subprocess.check_output([
            'curl','-fsS','--noproxy','*','--cacert',str(self.cert),'--resolve',
            f"deploy.example:{self.backends['sub']}:127.0.0.1",'--max-time','15',
            f"https://deploy.example:{self.backends['sub']}/subscription/fixture?provider=1"],timeout=20)
        stop(process)
        # x-ui gracefully stops its real Xray child; the generated config is authoritative.
        self.server = json.loads((bindir/'config.json').read_text())
        self.server.setdefault('log', {})['loglevel']='debug'
        self.validate(self.server,'server')
        self.client_configs = self.json_subscription if isinstance(self.json_subscription,list) else [self.json_subscription]
        self.assertEqual(len(self.client_configs),5, 'all managed profiles must be public')
        self.clients = {}
        for config in self.client_configs:
            outbound = next(o for o in config['outbounds'] if o['protocol'] in ('vless','trojan','hysteria'))
            stream = outbound['streamSettings']; network=stream['network']
            name = 'reality' if stream['security']=='reality' else {'ws':'ws','xhttp':'xhttp','grpc':'trojan-grpc','hysteria':'hysteria2'}[network]
            self.validate(config, name+'-public-subscription')
            self.clients[name] = config
        self.assertEqual(set(self.clients),set(self.rows))
        if 'Config validation' not in self.passed:
            self.passed.append('Config validation')

    def nginx_config(self):
        shared = render(SHARED, ws_port=str(self.backends['ws']),trojan_port=str(self.backends['trojan-grpc']))
        # Match backend relationship while keeping the seeded public paths unchanged.
        shared = shared.replace(f"/{self.backends['ws']}/websocket",'/10003/websocket').replace(
            f"/{self.backends['trojan-grpc']}/trojan",'/10004/trojan').replace('/dev/shm/uds2023.sock',str(self.uds))
        (self.root/'shared.conf').write_text(shared)
        main = render(MAIN,panel_port=str(self.backends['panel']),sub_port=str(self.backends['sub']))
        main = main.replace('127.0.0.1:7443',f"127.0.0.1:{self.backends['tls']}")
        main = main.replace('/etc/nginx/snippets/includes.conf',str(self.root/'shared.conf'))
        main = re.sub(r'include /etc/nginx/snippets/x-ui-auto-optional/\*\.conf;', '', main)
        main = re.sub(r'/etc/letsencrypt/live/[^/]+/fullchain.pem',str(self.cert),main)
        main = re.sub(r'/etc/letsencrypt/live/[^/]+/privkey.pem',str(self.key),main)
        main = main.replace('/var/www',str(self.root/'www'))
        stream = render('cat > /etc/nginx/stream-enabled/stream.conf')
        stream = stream.replace('127.0.0.1:8443',f"127.0.0.1:{self.backends['reality']}").replace(
            '127.0.0.1:7443',f"127.0.0.1:{self.backends['tls']}")
        stream = stream.replace('listen     443;',f'listen 127.0.0.1:{self.public};').replace('listen     [::]:443;','')
        temp = '\n'.join(f'{kind}_temp_path {self.root}/{kind};' for kind in ('client_body','proxy','fastcgi','uwsgi','scgi'))
        maps = render('cat > /etc/nginx/sites-available/00-maps.conf')
        cover = f"server {{ listen 127.0.0.1:{self.backends['cover']} ssl http2; ssl_certificate {self.cert}; ssl_certificate_key {self.key}; ssl_protocols TLSv1.3; return 200 'cover'; }}"
        config = f'''load_module /usr/lib/nginx/modules/ngx_stream_module.so;
worker_processes 1; daemon off; pid {self.root}/nginx.pid; error_log {self.root}/nginx-error.log;
events {{ worker_connections 256; }}
stream {{ {stream} }}
http {{ access_log off; {temp} {maps} {main} {cover} }}
'''
        path = self.root/'nginx.conf';path.write_text(config)
        result = subprocess.run([self.nginx,'-t','-p',str(self.root)+'/', '-c',path],text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        process = self.launch([self.nginx,'-p',str(self.root)+'/', '-c',path],'nginx')
        self.ready(process,('127.0.0.1',self.public))

    def client(self, name, negative=False):
        config = copy.deepcopy(self.clients[name])
        outbound = next(o for o in config['outbounds'] if o['protocol'] in ('vless','trojan','hysteria'))
        settings,stream = outbound['settings'],outbound['streamSettings']
        dest = settings['servers'][0] if 'servers' in settings else settings
        self.assertEqual((dest['address'],dest['port']),('deploy.example',443))
        dest.update(address='127.0.0.1',port=self.hysteria_port if name=='hysteria2' else self.public)
        if stream['security']=='tls':
            self.assertEqual(stream['tlsSettings'].get('serverName') or 'deploy.example','deploy.example')
            stream['tlsSettings']['serverName']='deploy.example'
            stream['tlsSettings']['allowInsecure']=False
            stream['tlsSettings']['certificates']=[{'certificateFile':str(self.cert),'usage':'verify'}]
        if negative:
            if name=='reality': stream['realitySettings']['shortId']='ffffffffffffffff'
            elif name=='xhttp': stream['xhttpSettings']['path']='/wrong-path'
            elif name=='ws': stream['wsSettings']['path']='/wrong-path'
            elif name=='trojan-grpc': stream['grpcSettings']['serviceName']='/wrong-service'
            else: stream['hysteriaSettings']['auth']='wrong-auth'
        socks=port(self.allocated_ports)
        config.setdefault('log', {})['loglevel']='debug'
        config['inbounds']=[{'listen':'127.0.0.1','port':socks,'protocol':'socks','settings':{'auth':'noauth'}}]
        config['outbounds']=[outbound]
        config.pop('routing',None);config.pop('dns',None)
        path=self.validate(config, name+('-negative' if negative else '-positive'))
        process=self.launch([self.xray,'run','-c',path],name+('-bad-client' if negative else '-client'))
        self.ready(process,('127.0.0.1',socks))
        before=len(self.origin_requests)
        result=subprocess.run(['curl','-fsS','--trace-ascii',str(self.root/(name+'-curl.trace')),'--noproxy','','--socks5-hostname',f'127.0.0.1:{socks}',
                '--connect-timeout','5','--max-time','12',f'http://127.0.0.1:{self.origin.server_port}/{name}'],
                capture_output=True,text=True,timeout=18)
        stop(process)
        if negative:
            self.assertNotEqual(result.returncode,0,'bad transport credentials/path unexpectedly worked')
            self.assertNotIn(self.marker,result.stdout)
            self.assertEqual(len(self.origin_requests),before,'negative transport reached origin')
        else:
            self.assertEqual(result.returncode,0,(self.root/(name+'-curl.trace')).read_text()[-1000:]+result.stderr+'; '+(self.root/(name+'-client.log')).read_text()[-1500:]+'; server '+(self.root/'xray-server.log').read_text()[-2500:])
            self.assertEqual(result.stdout,self.marker)
            self.assertEqual(len(self.origin_requests),before+1)

    def transport(self,name):
        self.nginx_config()
        process=self.launch([self.xray,'run','-c',self.root/'server.json'],'xray-server')
        if name == 'xhttp':
            self.ready(process,path=self.uds)
            self.assertTrue(self.uds.is_socket(),'UDS must be created by the real Xray process')
        elif name == 'hysteria2':
            self.ready(process,udp_port=self.hysteria_port)
        else:
            self.ready(process,('127.0.0.1',self.backends[name]))
        self.client(name)
        self.client(name,negative=True)
        self.passed.append(name+' positive/negative')

    def test_reality(self): self.transport('reality')
    def test_xhttp(self): self.transport('xhttp')
    def test_websocket(self): self.transport('ws')
    def test_trojan_grpc(self): self.transport('trojan-grpc')

    def grpc_fragmented(self, direct, count):
        if not direct: self.nginx_config()
        server = self.launch([self.xray,'run','-c',self.root/'server.json'],'xray-server')
        self.ready(server,('127.0.0.1',self.backends['trojan-grpc']))
        raw_link = next(urlsplit(line) for line in base64.b64decode(self.raw).decode().splitlines()
                        if urlsplit(line).scheme == 'trojan')
        query = parse_qs(raw_link.query)
        for consumer in ('JSON','RAW') if not direct else ('JSON',):
            config = copy.deepcopy(self.clients['trojan-grpc'])
            outbound = next(o for o in config['outbounds'] if o['protocol']=='trojan')
            endpoint, stream = outbound['settings']['servers'][0], outbound['streamSettings']
            if consumer == 'RAW':
                self.assertEqual(query['type'],['grpc'])
                self.assertEqual((raw_link.hostname,raw_link.port),('deploy.example',443))
                self.assertEqual(raw_link.username,endpoint['password'])
                self.assertEqual(query['serviceName'],[stream['grpcSettings']['serviceName']])
                endpoint['password'] = raw_link.username
                stream['grpcSettings']['serviceName'] = query['serviceName'][0]
            endpoint.update(address='127.0.0.1',port=self.backends['trojan-grpc'] if direct else self.public)
            if direct:
                stream['security']='none'; stream.pop('tlsSettings',None)
            else:
                stream['tlsSettings'].update(serverName='deploy.example',allowInsecure=False,
                                             certificates=[{'certificateFile':str(self.cert),'usage':'verify'}])
            socks = port(self.allocated_ports)
            config['inbounds']=[{'listen':'127.0.0.1','port':socks,'protocol':'socks','settings':{'auth':'noauth'}}]
            config['outbounds']=[outbound]; config.pop('routing',None); config.pop('dns',None)
            path = self.validate(config,'fragmented-'+consumer)
            client = self.launch([self.xray,'run','-c',path],'fragmented-'+consumer)
            self.ready(client,('127.0.0.1',socks))
            try:
                for iteration in range(count if direct else count//2):
                    nonce = '/'+consumer+'-'+str(iteration)+'-'+os.urandom(8).hex()
                    def exact(connection,size):
                        data=b''
                        while len(data)<size:
                            chunk=connection.recv(size-len(data))
                            if not chunk: raise EOFError('SOCKS handshake EOF')
                            data+=chunk
                        return data
                    with socket.create_connection(('127.0.0.1',socks),timeout=5) as connection:
                        connection.settimeout(15)
                        connection.sendall(b'\x05\x01\x00')
                        self.assertEqual(exact(connection,2),b'\x05\x00')
                        connection.sendall(b'\x05\x01\x00\x01'+socket.inet_aton('127.0.0.1')+
                                           self.origin.server_port.to_bytes(2,'big'))
                        reply=exact(connection,4); self.assertEqual(reply[:2],b'\x05\x00')
                        exact(connection,4 if reply[3]==1 else 16 if reply[3]==4 else exact(connection,1)[0])
                        exact(connection,2)
                        connection.sendall(f'GET {nonce} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n'.encode())
                        received=b''
                        while chunk:=connection.recv(65536): received+=chunk
                    # Save the unparsed response before any assertion/HTTP interpretation.
                    (self.root/'fragmented-received.bin').write_bytes(received)
                    expected=self.origin_responses[nonce]
                    (self.root/'fragmented-expected.bin').write_bytes(expected)
                    self.assertEqual(received,expected,f'{consumer} fragmented iteration {iteration+1}')
                    self.assertEqual(self.origin_requests.count(nonce),1)
            finally: stop(client)
        self.passed.append(f'gRPC fragmented {"direct" if direct else "nginx RAW/JSON"}: {count}')

    def test_grpc_fragmented_full(self): self.grpc_fragmented(False,100)
    def test_grpc_fragmented_direct(self): self.grpc_fragmented(True,50)
    def test_hysteria2(self):
        self.transport('hysteria2')
        self.assertIn(f'dialing to udp:127.0.0.1:{self.hysteria_port}',
                      (self.root/'hysteria2-client.log').read_text())
        # Actual Xray Hysteria configuration is UDP; no TCP listener may substitute it.
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1',self.hysteria_port),timeout=0.3)

    def test_public_subscription_semantics(self):
        raw=base64.b64decode(self.raw,validate=True).decode()
        links=[urlsplit(line) for line in raw.splitlines() if line]
        self.assertEqual(len(links),5)
        for link in links:
            self.assertIn(link.scheme,('vless','trojan','hysteria2'))
            self.assertEqual((link.hostname,link.port),('deploy.example',443))
            self.assertTrue(link.username == (self.uuid if link.scheme=='vless' else self.auth), 'public credential mismatch')
            query=parse_qs(link.query)
            if link.scheme == 'vless': self.assertEqual(query.get('encryption'),['none'])
            self.assertIn(query.get('sni',[link.hostname])[0] or link.hostname,('deploy.example','cover.example'))
            if query.get('security')==['reality']:
                self.assertEqual(query['pbk'],[self.public_key]);self.assertIn(query['sid'][0],json.loads(self.rows['reality']['stream_settings'])['realitySettings']['shortIds'])
            if query.get('type')==['ws']: self.assertEqual(query['path'],['/10003/websocket'])
            if query.get('type')==['xhttp']: self.assertEqual(query['path'],['/Session7AbC'])
            if query.get('type')==['grpc']: self.assertEqual(query['serviceName'],['/10004/trojan|trojan-multi'])
        for name, config in self.clients.items():
            outbound=next(o for o in config['outbounds'] if o['protocol'] in ('vless','trojan','hysteria'))
            settings=outbound['settings'];stream=outbound['streamSettings']
            endpoint=settings['servers'][0] if 'servers' in settings else settings
            self.assertEqual((endpoint['address'],endpoint['port']),('deploy.example',443))
            if outbound['protocol']=='vless':
                self.assertTrue(settings['id']==self.uuid,'JSON UUID mismatch')
                self.assertEqual(settings['encryption'],'none')
            if name=='reality':
                reality=stream['realitySettings']
                self.assertEqual(reality['serverName'],'cover.example')
                self.assertTrue(reality['publicKey']==self.public_key,'REALITY public key mismatch')
                self.assertIn(reality['shortId'],self.short_ids)
                self.assertEqual(reality['fingerprint'],'firefox')
                self.assertEqual(settings['flow'],'xtls-rprx-vision')
            else:
                self.assertEqual(stream['tlsSettings'].get('serverName') or endpoint['address'],'deploy.example')
                if name=='ws':
                    self.assertEqual(stream['network'],'ws')
                    self.assertEqual(stream['wsSettings']['path'],'/10003/websocket')
                    self.assertEqual(stream['tlsSettings']['alpn'],['http/1.1'])
                elif name=='xhttp':
                    self.assertEqual(stream['network'],'xhttp')
                    self.assertEqual(stream['xhttpSettings']['path'],'/Session7AbC')
                    self.assertEqual(stream['xhttpSettings']['mode'],'stream-up')
                elif name=='trojan-grpc':
                    self.assertEqual(stream['network'],'grpc')
                    self.assertEqual(stream['grpcSettings']['serviceName'],'/10004/trojan|trojan-multi')
                    self.assertIs(stream['grpcSettings']['multiMode'],False)
                    self.assertTrue(endpoint['password']==self.auth,'JSON Trojan credential mismatch')
                else:
                    self.assertEqual(stream['network'],'hysteria')
                    self.assertEqual(settings['version'],2)
                    self.assertTrue(stream['hysteriaSettings']['auth']==self.auth,'JSON Hysteria credential mismatch')
                    self.assertEqual(stream['tlsSettings']['alpn'],['h3'])
        self.passed.append('Subscription semantics')

    def test_public_outputs_do_not_leak_internal_endpoints(self):
        raw=base64.b64decode(self.raw,validate=True).decode()
        # Public outbound endpoints only: local SOCKS inbounds are legal client state.
        provider=base64.b64decode(self.provider,validate=True).decode()
        self.assertEqual(len(provider.splitlines()),5)
        for link in (urlsplit(line) for output in (raw,provider) for line in output.splitlines()):
            self.assertEqual((link.hostname,link.port),('deploy.example',443))
        public_values=[raw,provider]
        for config in self.client_configs:
            for outbound in config['outbounds']:
                if outbound['protocol'] in ('vless','trojan','hysteria'):
                    public_values.append(json.dumps(outbound))
        for output in public_values:
            for forbidden in ('127.0.0.1','localhost','/dev/shm/','uds2023.sock'):
                self.assertNotIn(forbidden,output)
            internal_ports = {str(p) for p in self.backends.values()} | {'8443','7443','9443'}
            internal_ports.update(FIXTURE[k] for k in ('panel_port','sub_port','mtr_backend_port','ws_port','trojan_port'))
            for internal in internal_ports:
                # Numeric WS/gRPC path components are the public managed route, not endpoint ports.
                self.assertNotRegex(output,rf'(?:\"port\"\s*:\s*|:){internal}(?:\D|$)')
        self.passed.append('No internal endpoint leak')

    def test_real_mihomo_generated_template(self):
        # Execute the actual project endpoint that substitutes the subscription ID.
        template=self.root/'clash.yaml.tpl'
        template.write_text((ROOT/'assets/clash/clash.yaml').read_text().replace('${DOMAIN}','deploy.example').replace('${SUB_PATH}','subscription'))
        source=(ROOT/'assets/diagnostics/mtr-backend.py').read_text().replace('"/var/www/subpage/clash.yaml.tpl"',repr(str(template)))
        namespace={'__name__':'integration_clash'}
        exec(compile(source,'mtr-backend.py','exec'),namespace)
        server=ThreadingHTTPServer(('127.0.0.1',0),namespace['Handler'])
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        yaml=subprocess.check_output(['curl','-fsS','--noproxy','*','--max-time','10',f'http://127.0.0.1:{server.server_port}/api/clash?sub_id=fixture'],timeout=15)
        self.assertNotIn(b'${',yaml);self.assertIn(b'https://deploy.example/subscription/fixture?provider=1',yaml)
        path=self.root/'mihomo.yaml';path.write_bytes(yaml)
        result=subprocess.run([self.mihomo,'-t','-d',self.root/'mihomo-state','-f',path],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        # provider=1 intentionally returns base64 URIs, not a standalone YAML config.
        self.assertEqual(len(base64.b64decode(self.provider,validate=True).decode().splitlines()),5)
        self.passed.append('Mihomo validation')
        self.nginx_config()
        xray=self.launch([self.xray,'run','-c',self.root/'server.json'],'mihomo-xray')
        self.ready(xray,('127.0.0.1',self.backends['trojan-grpc']))
        # The real x-ui provider remains the input; only public test endpoints are relocated.
        provider=base64.b64decode(self.provider,validate=True).decode()
        self.assertEqual(provider.count('@deploy.example:443'),5)
        payload=base64.b64encode(provider.replace('@deploy.example:443',f'@deploy.example:{self.public}').encode())
        class Provider(BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200); handler.send_header('Content-Length',str(len(payload)))
                handler.end_headers(); handler.wfile.write(payload)
            def log_message(handler,*args): pass
        provider_server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider_server.serve_forever,daemon=True).start()
        self.addCleanup(provider_server.server_close);self.addCleanup(provider_server.shutdown)
        expression='(select(.network == "grpc") | .["grpc-opts"]["grpc-service-name"]) |= (split("|") | .[0])'
        snapshots=[]
        for enabled in (False,True):
            socks,controller=port(self.allocated_ports),port(self.allocated_ports)
            # Keep the actual generated template/provider overrides/groups. Replace external DNS/rules
            # and automatic probes only in this offline fixture, forcing the selected provider route.
            runtime=yaml.decode()
            for section in ('dns','rule-providers','rules'):
                runtime=re.sub(r'^'+section+r':.*?(?=^[A-Za-z][^\n]*:|\Z)','',runtime,flags=re.M|re.S)
            runtime=runtime.replace('mixed-port: 10000',f'mixed-port: {socks}').replace('allow-lan: true','allow-lan: false')
            runtime=runtime.replace('https://deploy.example/subscription/fixture?provider=1',
                                    f'http://127.0.0.1:{provider_server.server_port}/provider')
            runtime=runtime.replace('      enable: true','      enable: false').replace('    type: url-test','    type: select')
            runtime=re.sub(r'(url: )https://(?:www.gstatic.com|cp.cloudflare.com)/generate_204',
                           rf'\g<1>http://127.0.0.1:{self.origin.server_port}/health',runtime)
            if not enabled:
                runtime=runtime.replace("        - '"+expression+"'\n",'')
            # Read the *actual post-override mapping* through a harmless test-only name export.
            # Both variants use the same export. No test-side subscription/YAML proxy generator.
            runtime=runtime.replace('    health-check:',"        - '.name = tostring'\n    health-check:",1)
            runtime+=f'\nexternal-controller: 127.0.0.1:{controller}\nbind-address: 127.0.0.1\nrules:\n  - MATCH,🌍 VPN\n'
            runtime+='hosts:\n  deploy.example: 127.0.0.1\n'
            runtime+='tls:\n  custom-certifactes:\n    - '+json.dumps(self.cert.read_text())+'\n'
            path=self.root/f'mihomo-{enabled}.yaml';path.write_text(runtime)
            process=self.launch([self.mihomo,'-d',self.root/f'mihomo-{enabled}','-f',path],f'mihomo-{enabled}')
            self.ready(process,('127.0.0.1',controller));self.ready(process,('127.0.0.1',socks))
            def api(route,body=None):
                args=['curl','-fsS','--noproxy','*','--max-time','10']
                if body is not None: args+=['-X','PUT','--data-binary','@-','-H','Content-Type: application/json']
                return subprocess.check_output(args+[f'http://127.0.0.1:{controller}'+route],
                                               input=json.dumps(body).encode() if body is not None else None,timeout=15)
            deadline=time.monotonic()+15
            while True:
                proxies=json.loads(api('/providers/proxies/sub'))['proxies']
                if proxies: break
                self.assertIsNone(process.poll(),'Mihomo exited before provider readiness')
                if time.monotonic()>=deadline:
                    log=(self.root/f'mihomo-{enabled}.log').read_text().replace(self.auth,'[redacted]').replace(self.uuid,'[redacted]')
                    self.fail('Mihomo provider readiness deadline expired: '+log[-3000:])
                time.sleep(0.05)
            self.assertEqual(len(proxies),5)
            mappings={p['name'].split('\nname: ',1)[-1].split('\n',1)[0]:p['name'] for p in proxies}
            snapshots.append(mappings)
            grpc=next(p['name'] for p in proxies if 'type: trojan' in p['name'])
            api('/proxies/'+quote('🌍 VPN',safe=''),{'name':grpc})
            try:
                for iteration in range(10 if enabled else 1):
                    nonce='/mihomo-'+str(enabled)+'-'+str(iteration)+'-'+os.urandom(8).hex()
                    with socket.create_connection(('127.0.0.1',socks),timeout=5) as connection:
                        connection.settimeout(15)
                        def exact(size):
                            data=b''
                            while len(data)<size:
                                chunk=connection.recv(size-len(data))
                                if not chunk: raise EOFError('Mihomo SOCKS handshake EOF')
                                data+=chunk
                            return data
                        connection.sendall(b'\x05\x01\x00');self.assertEqual(exact(2),b'\x05\x00')
                        connection.sendall(b'\x05\x01\x00\x01'+socket.inet_aton('127.0.0.1')+self.origin.server_port.to_bytes(2,'big'))
                        reply=exact(4);self.assertEqual(reply[:2],b'\x05\x00')
                        exact(4 if reply[3]==1 else 16 if reply[3]==4 else exact(1)[0]);exact(2)
                        connection.sendall(f'GET {nonce} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n'.encode())
                        received=b''
                        while chunk:=connection.recv(65536): received+=chunk
                    if enabled:
                        self.assertIn(nonce,self.origin_responses,'Mihomo did not reach origin')
                        self.assertEqual(received,self.origin_responses[nonce],'Mihomo fragmented response differs')
                        self.assertEqual(self.origin_requests.count(nonce),1)
                    else:
                        self.assertNotIn(nonce,self.origin_requests,'Unmodified pipe must not reach Tun-only nginx route')
                        self.assertNotIn(self.marker.encode(),received)
            finally: stop(process)
        self.assertEqual(snapshots[0].keys(),snapshots[1].keys())
        for name,before in snapshots[0].items():
            after=snapshots[1][name]
            # Mihomo's URI converter injects a random WS User-Agent before either override.
            # Compare every supplied/profile field; normalize only that consumer-generated default.
            if 'network: ws\n' in before:
                before=re.sub(r'(?m)^        User-Agent: .+$','        User-Agent: [consumer-random]',before)
                after=re.sub(r'(?m)^        User-Agent: .+$','        User-Agent: [consumer-random]',after)
            expected=before.replace('grpc-service-name: /10004/trojan|trojan-multi','grpc-service-name: /10004/trojan') if 'type: trojan' in before else before
            self.assertTrue(after==expected,'Provider changed unrelated settings: '+name)
        self.assertTrue(any('grpc-service-name: /10004/trojan\n' in m for m in snapshots[1].values()))
        self.passed.append('Mihomo provider gRPC: 10 byte-exact responses; other profiles unchanged')
