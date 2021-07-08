#!/usr/bin/env python3
# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, BlockedStatus

from jinja2 import Template
import subprocess
import os
import shutil
import time
from datetime import datetime
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class AptMirrorCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.synchronize_action, self._on_synchronize_action)
        self.framework.observe(self.on.create_snapshot_action, self._on_create_snapshot_action)
        self.framework.observe(self.on.publish_snapshot_action, self._on_publish_snapshot_action)
        self.framework.observe(self.on.list_published_snapshots_action, self._on_list_published_snapshots_action)
        self.framework.observe(self.on.list_snapshots_action, self._on_list_snapshots_action)
        self.framework.observe(self.on.delete_snapshot_action, self._on_delete_snapshot_action)
        self.framework.observe(self.on.upgrade_snapshot_action, self._on_upgrade_snapshot_action)
        self.framework.observe(self.on.publish_relation_joined,
                               self._on_publish_relation_joined)

        self._stored.set_default(config={})

    def _on_publish_relation_joined(self, event):
        publish_path = "{}/publish".format(self._stored.config['base-path'])
        event.relation.data[self.model.unit].update({'path': publish_path})

    def _on_update_status(self, _):
        published_snapshot = self._get_published_snapshot()
        if published_snapshot:
            self.model.unit.status = \
                ActiveStatus("Publishes: {}".format(published_snapshot))
        else:
            path = self._stored.config['base-path'] + '/mirror'
            if os.path.isdir(path):
                stat = os.stat(path)
                self.model.unit.status = \
                    BlockedStatus("Last sync: {} not published".format(time.ctime(stat.st_mtime)))
            else:
                self.model.unit.status = BlockedStatus("Packages not synchronized")

    def _on_install(self, _):
        subprocess.check_output(["apt", "install", "-y", "apt-mirror"])

    def _on_config_changed(self, _):
        for key in self.model.config:
            if key not in self._stored.config:
                value = self.model.config[key]
                self._stored.config[key] = value
            if self.model.config[key] != self._stored.config[key]:
                value = self.model.config[key]
                logger.info("Setting {} to: {}".format(key, value))
                self._stored.config[key] = value
        if 'use-proxy' in self._stored.config and self._stored.config['use-proxy']:
            if 'JUJU_CHARM_HTTP_PROXY' in os.environ:
                self._stored.config['http_proxy'] = os.environ['JUJU_CHARM_HTTP_PROXY']
                self._stored.config['use_proxy'] = True
            if 'JUJU_CHARM_HTTPS_PROXY' in os.environ:
                self._stored.config['https_proxy'] = os.environ['JUJU_CHARM_HTTPS_PROXY']
                self._stored.config['use_proxy'] = True
        else:
            self._stored.config['use_proxy'] = False
        self._stored.config['mirror-list'] = self.model.config['mirror-list'].splitlines()
        self._stored.config['security-pocket-name'] = self.model.config['security-pocket-name']
        self._stored.config['enable-multisite-publish'] = self.model.config['enable-multisite-publish']
        self._render_config(self._stored.config)
        if 'cron-schedule' in self._stored.config and \
           self._stored.config['cron-schedule'] != 'None':
            self._setup_cron_job(self._stored.config)

    def _on_synchronize_action(self, event):
        logger.info("Syncing packages")
        try:
            start = time.time()
            subprocess.check_output(['apt-mirror'],
                                    stderr=subprocess.STDOUT)
            elapsed = time.time() - start
            logger.info("Sync complete, took {}s".format(elapsed))
            event.set_results({"time": elapsed})
        except subprocess.CalledProcessError as e:
            logger.info("Error {}".format(e.output))
            event.fail(e.output)
            return

    def _on_create_snapshot_action(self, event):
        snapshot_name = self._get_snapshot_name()
        logger.info("Create snapshot {}".format(snapshot_name))
        snapshot_name_path = "{}/{}".format(self._stored.config['base-path'], snapshot_name)
        mirror_path = "{}/mirror".format(self._stored.config['base-path'])
        mirrors = self._mirror_names()
        if not os.path.exists(snapshot_name_path):
            os.makedirs(snapshot_name_path)
        for dirpath, dirs, files in os.walk(mirror_path):
            if 'pool' in dirs:
                self._create_pool_symlink(dirpath, mirrors, mirror_path, snapshot_name_path)
            if 'dists' in dirs:
                self._copy_dists_directory(dirpath, mirrors, mirror_path, snapshot_name_path)

    def _on_delete_snapshot_action(self, event):
        snapshot = event.params["name"]
        logger.info("Delete snapshot {}".format(snapshot))
        shutil.rmtree("{}/{}".format(self._stored.config['base-path'], snapshot))

    def _on_list_published_snapshots_action(self, event):
        snapshots = []
        if not self._stored.config['enable-multisite-publish']:
            publish_path = "{}/publish".format(self._stored.config['base-path'])
            if os.path.islink(publish_path):
                bn = os.path.basename(os.readlink(publish_path))
                snapshots.append("{} [{}]".format("default", bn))
        else:
            publish_path = "{}/publish".format(self._stored.config['base-path'])
            if os.path.exists(publish_path):
                for f in os.listdir(publish_path):
                    symlink_path = "{}/{}".format(publish_path, f)
                    if os.path.islink(symlink_path):
                        bn = os.path.basename(os.readlink(symlink_path))
                        snapshots.append("{} [{}]".format(f, bn))
        logger.info("List published snapshots {}".format(snapshots))
        event.set_results({"published-snapshots": snapshots})

    def _on_list_snapshots_action(self, event):
        snapshots = []
        for directory in next(os.walk(self._stored.config['base-path']))[1]:
            if directory.startswith("snapshot-"):
                snapshots.append(directory)
        logger.info("List snapshots {}".format(snapshots))
        event.set_results({"snapshots": snapshots})

    def _on_publish_snapshot_action(self, event):
        name = event.params["name"]
        site_name = event.params["site-name"]
        logger.info("Publish snapshot {}".format(name))
        snapshot_path = "{}/{}".format(self._stored.config['base-path'], name)
        publish_path = "{}/publish".format(self._stored.config['base-path'])
        if not os.path.isdir(snapshot_path):
            event.fail("Snapshot does not exist")
            return
        if os.path.islink(publish_path):
            os.unlink(publish_path)
        if self._stored.config['enable-multisite-publish']:
            site_publish_path = "{}/{}".format(publish_path, site_name)
            if not os.path.exists(publish_path):
                os.mkdir(publish_path)
            if os.path.islink(site_publish_path):
                os.unlink(site_publish_path)
            os.symlink(snapshot_path, site_publish_path)
        else:
            # simply simlink the snapshot_path when multisite disabled
            os.symlink(snapshot_path, publish_path)
        event.set_results({name: publish_path})

    def _on_upgrade_snapshot_action(self, event):
        name = event.params["name"]
        upgrade_name = self._get_snapshot_name()
        logger.info("Upgrade snapshot from {}".format(name))
        snapshot_path = "{}/{}".format(self._stored.config['base-path'], name)
        upgrade_path = "{}/{}-security".format(self._stored.config['base-path'], upgrade_name)
        if not os.path.isdir(snapshot_path):
            event.fail("Snapshot does not exist")
            return
        mirror_path = "{}/mirror".format(self._stored.config['base-path'])
        mirrors = self._mirror_names()
        if not os.path.exists(upgrade_path):
            os.makedirs(upgrade_path)

        security_pocket_name = self._stored.config['security-pocket-name']
        if not security_pocket_name:
            security_pocket_name = "/security.ubuntu.com/"
        logger.info("Security pocket name = {}".format(security_pocket_name))
        # copy from mirror
        for dirpath, dirs, files in os.walk(mirror_path):
            if 'pool' in dirs:
                self._create_pool_symlink(dirpath, mirrors, mirror_path, upgrade_path)
            if 'dists' in dirs:
                # copy the dist tree from the mirror if it is matching
                # security pocket name
                if re.findall(security_pocket_name, dirpath):
                    self._copy_dists_directory(dirpath, mirrors, mirror_path, upgrade_path)
        # copy dist from source snapshot - preserve package versions
        for dirpath, dirs, files in os.walk(snapshot_path):
            if 'dists' in dirs:
                # copy the dist tree from the snaphsot if it is not matching
                # the security pocket name
                if not re.findall(security_pocket_name, dirpath):
                    self._copy_dists_directory(dirpath, mirrors, snapshot_path, upgrade_path)
        event.set_results({name: upgrade_path})

    def _render_config(self, config):
        with open('templates/mirror.list.j2') as f:
            t = Template(f.read())
        with open('/etc/apt/mirror.list', "wb") as f:
            b = t.render(opts=config).encode("UTF-8")
            f.write(b)

    def _setup_cron_job(self, config):
        with open('/etc/cron.d/{}'.format(self.model.app.name), "w") as f:
            f.write(
                "{} root apt-mirror\n".format(
                    config['cron-schedule']
                )
            )

    def _get_snapshot_name(self):
        return 'snapshot-{}'.format(datetime.now().strftime("%Y%m%d%H%M%S"))

    def _mirror_names(self):
        return [urlparse(mirror.split()[1]).hostname
                for mirror in self._stored.config['mirror-list']]

    def _get_published_snapshot(self):
        if not self._stored.config['enable-multisite-publish']:
            publish_path = "{}/publish".format(self._stored.config['base-path'])
            if os.path.islink(publish_path):
                return os.path.basename(os.readlink(publish_path))
        else:
            publish_path = "{}/publish".format(self._stored.config['base-path'])
            sites = []
            if os.path.exists(publish_path):
                for f in os.listdir(publish_path):
                    symlink_path = "{}/{}".format(publish_path, f)
                    if os.path.islink(symlink_path):
                        bn = os.path.basename(os.readlink(symlink_path))
                        sites.append("{} [{}]".format(f, bn))
            if len(sites) > 0:
                if len(sites) == 1:
                    return sites[0]
                else:
                    return "multiple sites"


    def _build_subtree(self, mirrors, root, path):
        # path relative to root directory
        subtree = os.path.relpath(root, path)
        # strip mirror name from the path
        if 'strip-mirror-name' in self._stored.config and \
           self._stored.config['strip-mirror-name']:
            for m in mirrors:
                if re.findall(r'^{}'.format(m), subtree):
                    subtree = os.path.relpath(subtree, m)
        # strip arbitrary component from the path
        if 'strip-mirror-path' in self._stored.config and \
           self._stored.config['strip-mirror-path']:
            if self._stored.config['strip-mirror-path'] in subtree:
                subtree = subtree.replace(self._stored.config['strip-mirror-path'],
                                          '')
        return subtree

    def _create_pool_symlink(self, dirpath, mirrors, mirror_path, snapshot_name_path):
        src_root = dirpath
        src_pool = "{}/pool".format(src_root)
        subtree = self._build_subtree(mirrors, src_root, mirror_path)
        dst_root = "{}/{}".format(snapshot_name_path, subtree)
        dst_pool = "{}/pool".format(dst_root)
        os.makedirs(dst_root, exist_ok=True)
        os.symlink(src_pool, dst_pool)
        logger.info('{} -> {}'.format(src_pool, dst_pool))

    def _copy_dists_directory(self, dirpath, mirrors, mirror_path, snapshot_name_path):
        src_root = dirpath
        src_dists = "{}/dists".format(src_root)
        subtree = self._build_subtree(mirrors, src_root, mirror_path)
        dst_root = "{}/{}".format(snapshot_name_path, subtree)
        dst_dists = "{}/dists".format(dst_root)
        os.makedirs(dst_root, exist_ok=True)
        shutil.copytree(src_dists, dst_dists)
        logger.info('{} -> {}'.format(src_dists, dst_dists))

if __name__ == "__main__":
    main(AptMirrorCharm)
