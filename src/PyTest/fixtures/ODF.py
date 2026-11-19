'''
ODF fixture for Ceph scenarios

Provide the ODF Ceph cluster of the Cluster Under Test (CUT), with the Ceph
toolbox enabled, and its CephFS MDS daemons to a test.
'''
from collections.abc import Callable, Generator
import dataclasses
import logging
import os
import sys
import time
from typing import Any, Optional

import pytest

from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.ODF.Ceph.Ceph import Ceph
from libs.OpenShift.LP.ODF.Ceph.MDS import MDS
from libs.OpenShift.OCP.Node.Node import Node


logs = logging.getLogger()
_DEF__FXT_CFG = NamedDict(
    kCfg=(
        os.environ.get('KUBECONFIG') or
        os.path.expanduser('~/.kube/config')
    ),
    # The `--kubeconfig` option will be overridden by the above `.kCfg`.
    cliCfg=NamedDict(
        ocCfg={},
    ),
    ns='openshift-storage',
    fxtInf=NamedDict(
        cephCfg=NamedDict(
            chkHealth=True,
            timeout=NamedDict(
                toolsReady='10m',
                # Recovery after a Node outage may include data backfill.
                healthOK=1800,
            ),
        ),
    ),
    testPars=NamedDict(),
)


@dataclasses.dataclass(frozen=True)
class FxtObjODF:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    ceph: Ceph
    fxtInf: NamedDict
    testPars: NamedDict
    cli: NamedDict
    ns: str


@pytest.fixture(name='fxtODF', scope='function')
def FxtODF(
    request: Any,
    fxtOCcli: Callable[..., Any],
) -> Generator[FxtObjODF, None, None]:
    '''
    Provide the ODF `Ceph` cluster wrapper and test parameters to a test.

    This is the complete Fixture Configuration reference (see
    `docs/guide--odf.md` for concise usage).
    Configure via indirect parametrize with `dict` and nested `NamedDict`:
        @pytest.mark.parametrize(
            'fxtODF,',
            [({
                'testPars': NamedDict(downMin=40),
            },)],
            indirect=True,
        )

        Fixture Configuration:
          - kCfg:
            The `KUBECONFIG` file (default: `KUBECONFIG` Env. Var. value
            (default: `'~/.kube/config'`)).
          - cliCfg:
            Configuration options for CLIs:
              - ocCfg:  Configuration options for `OCcli` (default: `{}`).
          - ns:
            ODF Namespace (default: `'openshift-storage'`).
          - fxtInf:
            Fixture settings:
              - cephCfg:
                Ceph configurations.
                  - chkHealth:
                    Require Ceph `HEALTH_OK` at setup (default: `True`).
                  - timeout:
                      - toolsReady:
                        Ceph toolbox availability timeout (default: `'10m'`).
                      - healthOK:
                        Ceph `HEALTH_OK` wait budget in sec. at `fxtMDS`
                        teardown (default: `1800`).
          - testPars:
            Parameters to be passed to test (default: `NamedDict()`).

    Setup enables the Ceph toolbox, if not already enabled, then checks the
    Ceph health. A setup failure is reported as a test error.
    Teardown disables the Ceph toolbox only if setup enabled it. The fixture
    does not restore any other cluster state changed by the test.

    :yield:
        A `FxtObjODF` where:
          - ceph:       A `Ceph` instance.
          - fxtInf:     The resolved fixture settings `NamedDict` (see Fixture
                        Configuration for details).
          - testPars:   A `NamedDict` of test-specific parameters.
          - cli:
            A `NamedDict` with:
              - oc:         A `OCcli` instance.
          - ns:         ODF Namespace.

    :raises RuntimeError:
        The Ceph toolbox is not available, or Ceph is not `HEALTH_OK` while
        `fxtInf.cephCfg.chkHealth` is set.
    '''
    fxtCfg = _DEF__FXT_CFG | NamedDict(**getattr(request, 'param', {}))
    cephCfg = fxtCfg.fxtInf.cephCfg
    oc = fxtOCcli(
        ns=fxtCfg.ns,
        **{**fxtCfg.cliCfg.ocCfg, 'kubeconfig': fxtCfg.kCfg},
    )
    ceph = Ceph(fxtCfg.ns, oc=oc)
    toolsOn = False
    try:
        toolsOn = ceph.EnableTools(timeout=cephCfg.timeout.toolsReady)
        if cephCfg.chkHealth:
            (status, checks) = ceph.Health()
            if (status != 'HEALTH_OK'):
                raise RuntimeError(
                    f'Ceph is not `HEALTH_OK`: `{status}` {sorted(checks)}.'
                )
        yield FxtObjODF(
            ceph=ceph,
            fxtInf=fxtCfg.fxtInf,
            testPars=fxtCfg.testPars,
            cli=NamedDict(oc=oc),
            ns=fxtCfg.ns,
        )
    finally:
        primExc = sys.exc_info()[1]
        try: ceph.DisableTools(toolsOn)
        except Exception as error:
            logs.error('Could not disable Ceph toolbox: %s', error)
            if (primExc is None): raise
        logs.info('The `fxtODF` fixture teardown complete.')


@dataclasses.dataclass
class MDSclnInf:
    '''
    MDS cleanup information, set by the test.

    :param upAt:
        The `time.monotonic()` value when the stopped kubelet is started again
        by its timer (default: `None`, the kubelet was not stopped).
    '''
    upAt: Optional[float] = None


@dataclasses.dataclass(frozen=True)
class FxtObjMDS:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    active: MDS
    standby: MDS
    stbyNode: Node
    cln: MDSclnInf


@pytest.fixture(name='fxtMDS', scope='function')
def FxtMDS(fxtODF: FxtObjODF) -> Generator[FxtObjMDS, None, None]:
    '''
    Provide the active and standby-replay MDS daemons of the CephFS to a test.

    Configure via `fxtODF` (see `FxtODF()` for details).

    Setup requires exactly one `up:active` and one `up:standby-replay` MDS,
    each with a `Running` Pod, and a positive integer
    `fxtInf.cephCfg.timeout.healthOK`. A setup failure is reported as a test
    error.

    Teardown runs only if the test stopped a kubelet (`cln.upAt` is set). It
    waits for the kubelet timer and the Node to be `Ready`, deletes the
    standby-replay daemon Pods, so that Rook recreates them, then waits for
    Ceph `HEALTH_OK`.

    :yield:
        A `FxtObjMDS` where:
          - active:     The `up:active` `MDS`.
          - standby:    The `up:standby-replay` `MDS`.
          - stbyNode:   The `Node` running the standby-replay MDS Pod.
          - cln:        A `MDSclnInf`, to be set by the test.

    :raises ValueError:     The `healthOK` timeout is invalid.
    :raises RuntimeError:
        A setup prerequisite is not met, or the teardown failed.
    '''
    (ceph, oc) = (fxtODF.ceph, fxtODF.cli.oc)
    healthTO = fxtODF.fxtInf.cephCfg.timeout.healthOK
    if (
        not isinstance(healthTO, int) or
        isinstance(healthTO, bool) or
        (healthTO <= 0)
    ): raise ValueError('The `healthOK` must be a positive integer (sec.).')
    states = ceph.MDSstates()
    roles = {
        role: [name for (name, state) in states.items() if (state == role)]
        for role in ('up:active', 'up:standby-replay')
    }
    if any((len(names) != 1) for names in roles.values()):
        raise RuntimeError(
            'Prerequisites not met: expected one active and one '
            f'standby-replay MDS, got: {states}.'
        )
    active = MDS(roles['up:active'][0], ceph)
    standby = MDS(roles['up:standby-replay'][0], ceph)
    nodeNames = {}
    for mds in (active, standby):
        (pod, nodeNames[mds.name]) = mds.Pod()
        if not pod:
            raise RuntimeError(
                f'Prerequisites not met: no Running Pod for MDS `{mds.name}`.'
            )
    stbyNode = Node(nodeNames[standby.name], oc=oc)
    cln = MDSclnInf()
    try:
        yield FxtObjMDS(
            active=active, standby=standby, stbyNode=stbyNode, cln=cln,
        )
    finally:
        primExc = sys.exc_info()[1]
        clnFail = []
        if (cln.upAt is not None):
            if ((remTO := (cln.upAt - time.monotonic())) > 0):
                logs.info(
                    'Waiting %d sec. for kubelet on Node `%s`.',
                    remTO, stbyNode.name,
                )
                time.sleep(remTO)
            if not stbyNode.WaitReady():
                clnFail.append(f'Node `{stbyNode.name}` is not Ready')
            if not oc.Run(
                'delete',
                'Pod',
                n=fxtODF.ns,
                l=f'app=rook-ceph-mds,ceph_daemon_id={standby.name}',
                ignore_not_found=True, timeout='5m', wait='true',
            ).success:
                clnFail.append(f'Could not delete MDS `{standby.name}` Pods')
            deadline = time.monotonic() + healthTO
            while True:
                try: (status, _) = ceph.Health()
                except RuntimeError: status = ''
                if (status == 'HEALTH_OK'): break
                if (time.monotonic() >= deadline):
                    clnFail.append(
                        f'Ceph is not `HEALTH_OK` after {healthTO} sec.'
                    )
                    break
                time.sleep(30)
        if clnFail:
            clnErr = 'MDS cleanup failed: ' + '; '.join(clnFail)
            if (primExc is None): raise RuntimeError(clnErr)
            logs.error('%s; preserving primary exception.', clnErr)
        logs.info('The `fxtMDS` fixture teardown complete.')
