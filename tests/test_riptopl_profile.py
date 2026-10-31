"""RiptOPL settings export: only keys RiptOPL reads, no PS2 address, no secrets."""
import os
import tempfile
import tkinter as tk
import unittest

from launcher import riptopl_export, riptopl_profile as rp

# Every key RiptOPL rebuild/main 4ec6c68 reads from conf_network.cfg
# (include/config.h CONFIG_NET_*, read in src/app/settingsio.cpp) and the
# network keys it reads from settings_riptopl.cfg (CONFIG_OPL_FILENAME).
RIPTOPL_NETWORK_KEYS = {
    "eth_linkmode", "ps2_ip_use_dhcp", "ps2_ip_addr", "ps2_netmask", "ps2_gateway", "ps2_dns",
    "smb_share_nb_addr", "smb_ip", "smb_share_use_nbns", "smb_share", "smb_user", "smb_pass",
    "smb_port", "http_ip", "http_port", "http_base_path", "nbd_default_export",
}
RIPTOPL_OPL_KEYS = {"network_protocol", "net_protocol_pick", "net_start_mode", "udpfs_access"}
PS2_OWN = {"eth_linkmode", "ps2_ip_use_dhcp", "ps2_ip_addr", "ps2_netmask", "ps2_gateway", "ps2_dns"}
IP = "192.168.1.20"


def keys(profile):
    return dict(profile.network), dict(profile.opl)


class BuildTests(unittest.TestCase):
    def test_every_exported_key_is_one_riptopl_reads(self):
        cases = [("smbv1", {}), ("udpfs", {"root_dir": "x"}), ("udpfs", {"block_device": "x"}),
                 ("udpbd", {}), ("http", {"port": "1200"})]
        for server, values in cases:
            network, opl = keys(rp.build(server, values, IP))
            self.assertLessEqual(set(network), RIPTOPL_NETWORK_KEYS - PS2_OWN, server)
            self.assertLessEqual(set(opl), RIPTOPL_OPL_KEYS, server)
            self.assertIn("network_protocol", opl, server)

    def test_smbv1_values_follow_the_card(self):
        network, opl = keys(rp.build("smbv1", {"share_name": "roms", "port": "1025"}, IP))
        self.assertEqual(network, {"smb_share_use_nbns": 0, "smb_ip": IP, "smb_port": 1025,
                                   "smb_share": "roms", "smb_user": "guest", "smb_pass": ""})
        self.assertEqual(opl["network_protocol"], rp.NET_PROTO_SMB)
        self.assertEqual(keys(rp.build("smbv1", {"take_445": True, "port": "1025"}, IP))[0]["smb_port"], 445)

    def test_a_password_is_never_exported(self):
        profile = rp.build("smbv1", {"username": "me", "password": "hunter2"}, IP)
        self.assertNotIn("smb_pass", dict(profile.network))
        self.assertNotIn("hunter2", rp.render(profile))
        self.assertTrue(any("password" in n for n in profile.notes))

    def test_smb2_and_smb3_are_refused(self):
        for server in ("smbv2", "smbv3"):
            with self.assertRaisesRegex(ValueError, "SMBv1"):
                rp.build(server, {}, IP)

    def test_no_pc_ip_no_export(self):
        for bad in ("", "0.0.0.0", "127.0.0.1", "169.254.1.1", "nonsense"):
            with self.assertRaises(ValueError, msg=bad):
                rp.build("smbv1", {}, bad)
        rp.build("udpfs", {}, "")  # broadcast protocols need no PC IP

    def test_bound_address_wins_and_is_reported(self):
        profile = rp.build("http", {"bind": "10.0.0.5"}, IP)
        self.assertEqual(dict(profile.network)["http_ip"], "10.0.0.5")
        self.assertTrue(profile.warnings)

    def test_port_mismatches_the_client_cannot_follow_are_warned(self):
        self.assertTrue(rp.build("udpfs", {"port": "0x1234"}, IP).warnings)
        self.assertFalse(rp.build("udpfs", {"port": "0xF5F6"}, IP).warnings)
        self.assertTrue(rp.build("udpbd", {"port": "4000"}, IP).warnings)
        with self.assertRaises(ValueError):
            rp.build("http", {"port": "70000"}, IP)

    def test_udpfs_access_follows_what_the_card_serves(self):
        self.assertEqual(keys(rp.build("udpfs", {"root_dir": "x"}, IP))[1]["udpfs_access"], 0)
        image = keys(rp.build("udpfs", {"block_device": "x"}, IP))[1]
        self.assertEqual((image["network_protocol"], image["udpfs_access"]), (rp.NET_PROTO_UDPFSBD, 1))

    def test_values_that_would_break_the_file_are_refused(self):
        for share in ("a\nb", "x" * 300):
            with self.assertRaises(ValueError):
                rp.build("smbv1", {"share_name": share}, IP)


class RenderTests(unittest.TestCase):
    def test_both_file_styles_match_riptopls_writers(self):
        text = rp.render(rp.build("smbv1", {"share_name": 'my "games"'}, IP))
        self.assertIn("smb_port=1025\n", text)
        self.assertIn("smb_port = 1025;\n", text)
        self.assertIn('smb_ip = "{}";'.format(IP), text)
        self.assertIn('smb_share = "my \\"games\\"";', text)
        self.assertIn('smb_pass = "";', text)
        self.assertIn("profile version {}".format(rp.PROFILE_VERSION), text)
        self.assertIn(rp.RIPTOPL_REF, text)
        self.assertNotIn("ps2_ip", text)
        self.assertIn("settings_riptopl.cfg (A: key=value)", text)
        self.assertNotIn("conf_opl.cfg", text)

    def test_window_text_explains_a_refusal(self):
        self.assertTrue(riptopl_export.profile_text("smbv2", {}, IP).startswith("Not exported:"))


class WindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("no display: {}".format(exc))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_window_switches_cards_and_saves_one_text_file(self):
        window = riptopl_export.RiptoplExport(self.root, {"smbv1": {}, "http": {"port": "1200"}}, IP)
        self.assertIn("smb_port=1025", window.text.get("1.0", "end"))
        window.choice.set("HTTP")
        window.refresh()
        self.assertIn("http_port=1200", window.text.get("1.0", "end"))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.txt")
            riptopl_export.write_text(path, window.current_text())
            with open(path, encoding="utf-8") as handle:
                self.assertIn("http_port=1200", handle.read())
            self.assertEqual(os.listdir(tmp), ["out.txt"])
        window.destroy()


if __name__ == "__main__":
    unittest.main()
