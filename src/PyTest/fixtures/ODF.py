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
from typing import Any

import pytest

from libs.CustomTypes import NamedDict
from libs.OpenShift.LP.ODF.Ceph.Ceph import Ceph
from libs.OpenShift.LP.ODF.Ceph.MDS import MDS
from libs.OpenShift.OCP.Infra.Node import Node


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
            ),
        ),
    ),
    testPars=NamedDict(),
)
_DEF__FXT_MDS_CFG = NamedDict(
    fsName='',
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
                'testPars': NamedDict(pollInt=30),
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


@dataclasses.dataclass(frozen=True)
class FxtObjMDS:
    '''
    Fixture Object.

    Can be sub-classed to add fields. Sub-classes must also be decorated with
    `@dataclass(frozen=True)`.
    '''
    fsName: str
    active: MDS
    standby: MDS
    activeNode: Node
    stbyNode: Node


@pytest.fixture(name='fxtMDS', scope='function')
def FxtMDS(request: Any, fxtODF: FxtObjODF) -> FxtObjMDS:
    '''
    Provide the active and standby-replay MDS daemons of one CephFS file
    system to a test.

    Configure via indirect parametrize with `dict`:
        @pytest.mark.parametrize(
            'fxtMDS,',
            [({'fsName': 'ocs-storagecluster-cephfilesystem'},)],
            indirect=True,
        )

        Fixture Configuration:
          - fsName:
            The CephFS file system name (default: `''`, the file system
            created by ODF, i.e. the `CephFilesystem` owned by the
            `StorageCluster`).

    The Ceph cluster and its CLI come from `fxtODF` (see `FxtODF()`).

    Setup requires, in the file system, exactly one `up:active` and one
    `up:standby-replay` MDS (the ODF default topology), each with a `Running`
    Pod. A setup failure is reported as a test error.
    The fixture changes nothing, so it has no teardown.

    :return:
        A `FxtObjMDS` where:
          - fsName:     The CephFS file system name.
          - active:     The `up:active` `MDS`.
          - standby:    The `up:standby-replay` `MDS`.
          - activeNode: The `Node` running the active MDS Pod.
          - stbyNode:   The `Node` running the standby-replay MDS Pod.

    :raises RuntimeError:   A setup prerequisite is not met.
    '''
    fxtCfg = _DEF__FXT_MDS_CFG | NamedDict(**getattr(request, 'param', {}))
    (ceph, oc) = (fxtODF.ceph, fxtODF.cli.oc)
    try: fsName = fxtCfg.fsName or ceph.ODFfsName()
    except RuntimeError as error:
        raise RuntimeError(f'Prerequisites not met: {error}') from None
    states = ceph.MDSstates(fsName)
    roles = {
        role: [name for (name, state) in states.items() if (state == role)]
        for role in ('up:active', 'up:standby-replay')
    }
    if any((len(names) != 1) for names in roles.values()):
        raise RuntimeError(
            'Prerequisites not met: expected one active and one '
            f'standby-replay MDS in `{fsName}`, got: {states}.'
        )
    active = MDS(roles['up:active'][0], ceph)
    standby = MDS(roles['up:standby-replay'][0], ceph)
    nodes = {}
    for mds in (active, standby):
        (pod, nodeName) = mds.Pod()
        if not pod:
            raise RuntimeError(
                f'Prerequisites not met: no Running Pod for MDS `{mds.name}`.'
            )
        nodes[mds.name] = Node(nodeName, oc=oc)
    return FxtObjMDS(
        fsName=fsName,
        active=active,
        standby=standby,
        activeNode=nodes[active.name],
        stbyNode=nodes[standby.name],
    )
