# ODF Ceph Fixtures

Use `fxtODF` to work with the ODF Ceph cluster of the Cluster Under Test (CUT), and `fxtMDS` to target its CephFS MDS daemons. The fixtures do not install
ODF. Ceph commands run inside the Ceph toolbox Pod via `oc exec`, not locally.


## Minimal Usage

```python
from fixtures.ODF import FxtObjMDS, FxtObjODF


def test_Failover(fxtODF: FxtObjODF, fxtMDS: FxtObjMDS) -> None:
    fxtODF.ceph.FailMDS(fxtMDS.active.name)
```

`fxtMDS` depends on `fxtODF`, so request both and configure them through `fxtODF`. This is a usage example, not a portable acceptance test. See the complete
[fixture configuration and parametrization](../fixtures/ODF.py#L64), the [`Ceph`](../libs/OpenShift/LP/ODF/Ceph/Ceph.py) and
[`MDS`](../libs/OpenShift/LP/ODF/Ceph/MDS.py) wrappers, and the [`Node`](../libs/OpenShift/OCP/Node/Node.py) wrapper for kubelet and host access.


## Configuration

- Use an OCP CUT with ODF Internal mode installed and cluster-admin access. `ns` defaults to `openshift-storage`.
- Setup enables the Ceph toolbox (`StorageCluster` `.spec.enableCephTools`) if it is not already enabled, and waits up to
  `fxtInf.cephCfg.timeout.toolsReady` (default: `10m`) for it. If the toolbox does not become available, setup disables it again.
- With `fxtInf.cephCfg.chkHealth` (default: `True`), setup requires Ceph `HEALTH_OK`. Muted Ceph health checks are ignored.
- `fxtMDS` requires exactly one `up:active` and one `up:standby-replay` MDS, each with a `Running` Pod. It yields both as `MDS` wrappers, the standby-replay
  Pod's Node as a `Node` wrapper, and a `cln` record for the test.
- A setup failure, such as an unavailable toolbox, unhealthy Ceph, or missing MDS roles, is reported as a test error, not a test failure.
- Configure via indirect parametrization of `fxtODF` with `ns`, `kCfg`, `cliCfg`, `testPars`, and nested `NamedDict` settings.


## Node Access and Kubelet Stop

`Node.RunOnHost()` runs host commands through the Node's `machine-config-daemon` Pod; no SSH access is needed. `Node.StopKubelet(downMin)` requires a `Ready`
Node, arms a host `systemd` timer, then stops the kubelet. The timer is the only way the kubelet comes back, after `downMin` minutes, because the Node is
unreachable through `oc` meanwhile. The `oc debug` Pod used for the stop is removed by Kubernetes after the Node is `Ready` again.


## Cleanup

`fxtODF` teardown disables the Ceph toolbox only if its setup enabled it. `fxtMDS` teardown runs only if the test set `cln.upAt` (the `time.monotonic()` value
when the kubelet timer fires): it waits for the timer and the Node to be `Ready`, deletes the standby-replay daemon Pods so that Rook recreates them, then waits
up to `fxtInf.cephCfg.timeout.healthOK` (a positive integer in sec., default: `1800`; checked at setup) for Ceph `HEALTH_OK`. Set `cln.upAt` before stopping a
kubelet, so that cleanup also runs if the stop fails part way. Neither fixture restores other cluster state changed by the test.

Kubelet stops and MDS failovers disrupt the whole storage cluster. Use a disposable CUT only.
