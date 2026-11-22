from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ForkRuntime(unittest.TestCase):
    def test_firewall_preserves_existing_rules_and_allows_ssh_before_deny(self):
        installer = (ROOT / 'install.sh').read_text()
        firewall = installer[installer.index('ufw_status='):]
        self.assertNotIn('ufw --force reset', firewall)
        self.assertLess(firewall.index('ufw allow "${SSH_PORT}/tcp"'),
                        firewall.index('ufw default deny incoming'))

    def test_stealth_and_ipv6_disabled(self):
        installer = (ROOT / 'install.sh').read_text()
        self.assertIn('net.ipv6.conf.all.disable_ipv6 = 1', installer)
        self.assertIn('net.ipv6.conf.default.disable_ipv6 = 1', installer)
        self.assertIn('net.ipv6.conf.lo.disable_ipv6 = 1', installer)
        self.assertIn('net.ipv4.icmp_echo_ignore_all = 1', installer)
        self.assertIn("sed -i 's/^IPV6=.*/IPV6=no/' /etc/default/ufw", installer)
        self.assertNotIn("grep -qx 'IPV6=yes' /etc/default/ufw", installer)

    def test_credentials_stay_private_without_terminal_dump(self):
        installer = (ROOT / 'install.sh').read_text()
        self.assertNotIn('cat /root/server_credentials.txt', installer)
        self.assertNotRegex(installer, r'chmod 644 .*privkey\.pem')
        self.assertNotIn('chmod 755 /root ', installer)

    def test_panel_and_subscription_upstreams_use_verified_tls(self):
        template = (ROOT / 'assets/nginx/vhost.conf.template').read_text()
        self.assertEqual(template.count('proxy_pass https://127.0.0.1:__PANEL_PORT__;'), 1)
        self.assertEqual(template.count('proxy_pass https://127.0.0.1:__SUB_PORT__;'), 3)
        self.assertNotIn('proxy_pass http://127.0.0.1:__PANEL_PORT__;', template)
        self.assertNotIn('proxy_pass http://127.0.0.1:__SUB_PORT__;', template)
        self.assertIn('proxy_ssl_server_name on;', template)
        self.assertIn('proxy_ssl_name __DOMAIN__;', template)
        self.assertIn('proxy_ssl_verify on;', template)
        self.assertIn('proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;', template)
