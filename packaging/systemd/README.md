# systemd deployment

```sh
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin ps2edge
sudo install -m 0755 ps2servers-edge /usr/local/bin/ps2servers-edge
sudo install -m 0644 packaging/systemd/ps2servers-edge.service /etc/systemd/system/
sudo install -m 0644 packaging/systemd/ps2servers-edge.env /etc/default/ps2servers-edge
sudo mkdir -p /srv/ps2
sudo chown root:ps2edge /srv/ps2
sudo chmod 0750 /srv/ps2
sudo systemctl daemon-reload
sudo systemctl enable --now ps2servers-edge
```

Set `PS2EDGE_ROOT` to the mounted game directory before enabling the unit. The
unit intentionally exposes the root read-only and runs without administrator
privileges. If the directory is on removable storage, add the corresponding
mount unit to `After=`/`RequiresMountsFor=` or override `ConditionPathIsDirectory`.
