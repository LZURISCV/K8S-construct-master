#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo '请使用 root' >&2; exit 1; }
[[ -f /etc/kubernetes/kubelet.conf.before-rv-migration ]] || { echo '找不到安装前配置' >&2; exit 1; }
cp -p /etc/kubernetes/kubelet.conf.before-rv-migration /etc/kubernetes/kubelet.conf
rm -f /etc/systemd/system/kubelet.service.d/30-rv-migration.conf
systemctl daemon-reload
systemctl restart kubelet
systemctl disable --now rv-migration-proxy
echo '已切回安装前的 CRI 端点；迁移检查点保留。'
