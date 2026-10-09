"""Optional AWG installer contracts; no host installation is executed."""
import os
from pathlib import Path
import subprocess
import unittest
from test_personal_xhttp import function, SOURCE

class AmneziaWG(unittest.TestCase):
    def test_opt_in_defaults_and_eof(self):
        for answer, expected in [('', 'n'), ('\n','n'), ('N\n','n'), ('y\n','y'), ('Y\n','y'), ('invalid\nN\n','n')]:
            result = subprocess.run(['bash','-eu','-c', 'msg_inf() { :; }; '+function('select_amneziawg')+'; select_amneziawg; echo RESULT=$INSTALL_AWG'],input=answer,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('RESULT='+expected,result.stdout)
    def test_preflight_precedes_cleanup_and_does_not_create_client(self):
        main=function('main')
        self.assertLess(main.index('preflight_amneziawg'),main.index('cleanup_adguard'))
        self.assertLess(main.index('select_amneziawg'),main.index('select_adguard'))
        self.assertIn('3X-UI AUTO NGINX',SOURCE)
        helper=Path(__file__).resolve().parents[1]/'assets/amneziawg/managed.py'
        self.assertTrue(helper.exists())
        self.assertIn("'settings': {}",helper.read_text())

    def test_port_conflict_is_rejected_without_mutation(self):
        import socket
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as occupied:
            occupied.bind(('127.0.0.1',8443))
            result=subprocess.run(['bash','-eu','-c',function('preflight_amneziawg')+'; PANEL_TAG=v3.9.0; INSTALL_AWG=y; preflight_amneziawg; echo DESTRUCTIVE'],text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn('DESTRUCTIVE',result.stdout)
        free=subprocess.run(['bash','-eu','-c',function('preflight_amneziawg')+'; PANEL_TAG=v3.9.0; INSTALL_AWG=y; preflight_amneziawg'],capture_output=True)
        self.assertEqual(free.returncode,0,free.stderr)
        result=subprocess.run(['bash','-eu','-c',function('preflight_amneziawg')+'; INSTALL_AWG=n; preflight_amneziawg'],capture_output=True)
        self.assertEqual(result.returncode,0)

    def test_native_validation_failure_removes_created_record(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('awg',Path(__file__).resolve().parents[1]/'assets/amneziawg/managed.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        panel=object.__new__(module.Panel)
        with patch.object(panel,'request',side_effect=[{'obj':{'id':42,'protocol':'invalid'}},{'success':True}]) as request:
            with self.assertRaises(ValueError):panel.create('panel.example.com')
            self.assertEqual(request.call_args.args,('panel/api/inbounds/del/42',{}))
            body=request.call_args_list[0].args[1]
            self.assertEqual((body['port'],body['tag'],body['settings']),(8443,'inbound-8443-udp',{}))
        with patch.object(panel,'create',return_value={'id':42}), patch.object(panel,'request') as request, patch.object(module.subprocess,'run') as run:
            run.side_effect=[subprocess.CompletedProcess([],0,stdout=''),subprocess.CalledProcessError(1,['ufw']),subprocess.CompletedProcess([],0)]
            with self.assertRaises(ValueError):module.configure(panel,'panel.example.com')
            request.assert_called_once_with('panel/api/inbounds/del/42',{})
            self.assertEqual(run.call_args.args[0],['ufw','--force','delete','allow','8443/udp'])

    def test_decline_does_not_configure_or_show_warning(self):
        main=function('main')
        self.assertIn('if [[ "${INSTALL_AWG:-n}" == y ]]',main)
        self.assertNotIn('8443/udp',function('setup_firewall'))
        summary=function('show_results')
        self.assertIn('Configured (UDP/8443)',summary)
        self.assertNotIn('AmneziaWG              Running',summary)

    def test_summary_describes_configuration_without_claiming_listener(self):
        helpers='\n'.join(function(n) if n == 'show_results' else '' for n in ('show_results',))
        messages='msg_ok() { printf "\\033[32m%s\\033[0m\\n" "$*"; }; msg_warn() { printf "\\033[33m%s\\033[0m\\n" "$*"; }; msg_inf() { echo "$*"; }; ufw() { echo "Status: active"; };'
        for state in ('configured','failed','not_requested'):
            result=subprocess.run(['bash','-c',messages+helpers+'; show_results'],text=True,capture_output=True,
                env={**os.environ,'AWG_RESULT':state,'domain':'panel.example.com','reality_domain':'reality.example.com',
                     'panel_path':'fixture','config_username':'user','config_password':'password'})
            self.assertEqual(result.returncode,0,result.stderr)
            lines=[line for line in result.stdout.splitlines() if 'AmneziaWG' in line]
            if state == 'not_requested':self.assertEqual(lines,[])
            elif state == 'configured':
                self.assertIn('Configured (UDP/8443)',lines[0]);self.assertIn('\x1b[32m',lines[0])
            else:
                self.assertIn('Not configured',lines[0]);self.assertIn('\x1b[33m',lines[0])
            self.assertIn('Password: password',result.stdout)

    def test_api_credentials_use_stdin_and_private_temporary_state(self):
        import importlib.util
        import io
        import json
        import tempfile
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('awg',Path(__file__).resolve().parents[1]/'assets/amneziawg/managed.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            replies=[{'success':True,'obj':'csrf'}, {'success':True}, {'success':True,'obj':'csrf'}]
            with patch.object(module.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,stdout=json.dumps(r).encode()) for r in replies]) as run:
                module.Panel('https://panel.example.com/path/','admin','unique-private-password',directory)
                for call in run.call_args_list:
                    self.assertNotIn('unique-private-password',' '.join(call.args[0]))
                self.assertIn(b'unique-private-password',run.call_args_list[1].kwargs['input'])
                self.assertEqual((Path(directory)/'cookies').stat().st_mode & 0o777,0o600)
        self.assertFalse(Path(directory).exists())
        directories=[]
        def fail(*args):
            directories.append(args[3]);raise ValueError('fixture login failed')
        with patch.object(module,'Panel',side_effect=fail),patch.object(module.sys,'argv',['managed.py','panel.example.com','path','admin']),patch.object(module.sys,'stdin',io.StringIO('unique-private-password')):
            with self.assertRaises(ValueError):module.main()
        self.assertTrue(directories)
        self.assertFalse(Path(directories[0]).exists())
