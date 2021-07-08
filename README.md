# Apt Mirror

## Description

A small tool that provides ability to mirror any parts (or even all) of Debian and Ubuntu GNU/Linux distributions or any other apt sources which typically are provided by open source developers.

## Usage

### Deployment
The charm can be deployed using `Juju`:
```
juju deploy cs:apt-mirror
```

The charm can handle arbitrary set of upstream DEB sources via setting `mirror-list`. Example below shows a bundle with this charm configured to mirror multiple Ubuntu series (Bionic and Focal) in a single repository and expose this repository via NGINX. Additionally PPAs and external repositories can be mirrored.
```
series: bionic
machines:
  '0':
    series: bionic
services:
  nginx:
    charm: cs:~majduk/nginx
    expose: true
    num_units: 1
    to:
    - '0'
  apt-mirror:
    charm: cs:~marton-kiss/apt-mirror
    expose: true
    num_units: 1
    options:
      mirror-list: |-
        deb http://archive.ubuntu.com/ubuntu bionic main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu bionic-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu bionic-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu bionic-security main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu focal-security main restricted universe multiverse
    to:
    - '0'
relations:
- - nginx
  - apt-mirror
```

The repository needs to be exposed over HTTP using some web server. In the example above, Nginx charmed HTTP server is used to expose the repository.

### Repository consumption

The clients can be pointed to the repository by simply updating the `/etc/apt/sources.list` file to point to the repository, for example `repo.example.com`:
```
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal main restricted universe multiverse
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal-backports main restricted universe multiverse
deb http://repo.example.com/security.ubuntu.com/ubuntu focal-security main restricted universe multiverse
```

Additional notes:
- The repository can be consumed by units deploed by MAAS. Please refer to [MAAS documentation](https://maas.io/docs/deb/2.9/ui/package-repositories) for the detailed MAAS configuration options.
- The repository can be consumed by Juju deployed models. Please refer to the [discourse](https://discourse.charmhub.io/t/offline-mode-strategies/1071) on offline deployment strategies and [Juju documentation](https://discourse.charmhub.io/t/configuring-models/1151) for more details. In order to use the repository from the Juju model level, simplistic approach is just to use Juju model `apt-mirror` config option and `install-sources` config options for the charms being used.

### Repository management

The repository exposes single, selected snapshot to the clients. After the repository is deployed, it is necessary to pull the upstream packages to the repository:
```
juju run-action apt-mirror/0 synchronize
```
When the action execution completes (depending on the configured mirror list and available bandwidth, it can take a considerable amount of time), snapshot can be created:
```
juju run-action --wait apt-mirror/0 create-snapshot
```
Output of the action contains the created snapshot name, for example `snapshot-20210329092856`.

This snapshot can in turn be published to be used by the clients:
```
juju run-action --wait apt-mirror/0 publish-snapshot name=snapshot-20210329092856
```

Full list of available snapshots can be obtained by running:
```
juju run-action --wait apt-mirror/0 list-snapshots
```
Currently published snapshot is shown in `juju status`. 

Repository can be synchronized with the upstream multiple times and multiple snapshots can be created. It's possible to expose any arbitrary snapshot, making it possible to fine tune the packages available to the repository cilents.

The repository allows also specifying a Cron job via `cron-schedule` option, to regularily, automatically sync to the upstream to make sure the repository tracks upstream at a certain delay. To expose the latest packages to the clients, snapshot still needs to be created and published.

### Security pocket upgrades

If the apt mirror list contains the security.ubuntu.com archive, it is possible to upgrade a snapshot with the packages from the security package repository only and the keep the main and other packages pinned into their original stable versions.

A snapshot upgrade can be obtained by using the upgrade-snapshot action, where the name is set to the base snapshot name:
```
juju run-action --wait upgrade-snapshot name=snapshot-20210329092856
```

In this case the pool will be symlinked to the recent mirror, the security dists directory will be fetched from the recent mirror as well, meanwhile other package descriptor dist directories will be directly cloned from the base snapshot.

The security pocket name defaults to `/security.ubuntu.com/` and it can be set to any other custom value using:
```
juju config apt-mirror security-pocket-name=<some-custom-regex-string>
```

The upgrade snapshot action is using this value to decide which dists directories will be cloned from the recent mirror, and which ones will come from the base snapshot.


### Multi site support

It is possible to assign snapshots to multiple sites and those symlinks will be available under the published directory.

Enable the multi site feature:
```
juju config apt-mirror enable-multisite-publish=true
```

Publish snapshots under site names:
```
juju run-action --wait apt-mirror/0 publish-snapshot name=snapshot-20210708084914-security site-name=site01.example.com
juju run-action --wait apt-mirror/0 publish-snapshot name=snapshot-20210708084914-security site-name=site02.example.com
juju run-action --wait apt-mirror/0 publish-snapshot name=snapshot-20210708084914 site-name=site03.example.com
```

Enlist site snapshot assignments:
```
juju run-action --wait apt-mirror/0 list-published-snapshots
unit-apt-mirror-0:
  UnitId: apt-mirror/0
  id: "55"
  results:
    published-snapshots: '[''site01.example.com [snapshot-20210708085248-security]'',
      ''site03.example.com [snapshot-20210708084914]'', ''site02.example.com [snapshot-20210708085248-security]'']'
  status: completed
  timing:
    completed: 2021-07-08 10:47:02 +0000 UTC
    enqueued: 2021-07-08 10:46:56 +0000 UTC
    started: 2021-07-08 10:47:01 +0000 UTC
```

## Developing

Create and activate a virtualenv,
and install the development requirements,

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Testing

Just run `run_tests`:

    ./run_tests
