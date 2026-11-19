import logging
import time
from typing import TYPE_CHECKING

import pytest

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.Node.Node import Node


if TYPE_CHECKING:
    from fixtures.ODF import FxtObjMDS, FxtObjODF


logs = logging.getLogger()


class TestMDS():
    '''ODF CephFS MDS resiliency scenarios.'''

    @pytest.mark.odf
    @pytest.mark.parametrize(
        'fxtODF,',
        [({
            'testPars': NamedDict(
                downMin=40,         # Kubelet stop duration (min.).
                pollInt=30,         # Poll interval (sec.).
                fdGrowMax=100,      # Max. allowed MDS FD growth.
                emfileWait=120,     # Wait for EMFILE once FD limit is hit.
                varMaxPct=85,       # Stop polling above this `/var` usage.
                settleSec=150,      # Settle time after kubelet is back.
                failoverTO=300,     # Max. CephFS degraded time on failover.
            ),
        },)],
        indirect=True,
    )
    def test_StandbyReplayFDleakAfterKubeletStop(
        self,
        fxtODF: 'FxtObjODF',
        fxtMDS: 'FxtObjMDS',
    ) -> None:
        '''
        Stop the kubelet on the standby-replay MDS Node. Rook starts a
        replacement standby-replay Pod while the original MDS keeps running,
        so two daemons share one name. Check the replacement MDS for a File
        Descriptor (FD) leak and `EMFILE`, then fail the active MDS and check
        that the CephFS failover completes.
        '''
        (ceph, oc, pars) = (fxtODF.ceph, fxtODF.cli.oc, fxtODF.testPars)
        (standby, stbyNode) = (fxtMDS.standby, fxtMDS.stbyNode)
        findings = []

        # Trigger. The kubelet comes back by its timer only (at `cln.upAt`).
        fxtMDS.cln.upAt = time.monotonic() + (pars.downMin * 60)
        assert stbyNode.StopKubelet(pars.downMin), (
            f'Could not stop the kubelet on Node `{stbyNode.name}`.'
        )

        # Observe the replacement MDS until the FD limit is hit and `EMFILE`
        # is logged, or until the kubelet is back.
        (fdFirst, fdMax, fdLim, emfile, limAt) = (None, 0, 0, 0, None)
        podSeen = False
        while (time.monotonic() < fxtMDS.cln.upAt):
            time.sleep(pars.pollInt)
            (pod, nodeName) = standby.Pod(exclNode=stbyNode.name)
            if not pod: continue
            podSeen = True
            node = Node(nodeName, oc=oc)
            if (fdStat := standby.FDstat(node)) is not None:
                if (fdFirst is None): fdFirst = fdStat[0]
                (fdMax, fdLim) = (max(fdMax, fdStat[0]), fdStat[1])
            emfile += max(0, standby.LogCount(
                pod,
                'too many open files|EMFILE',
                since=f'{pars.pollInt}s',
            ))
            logs.info(
                'MDS `%s` Pod `%s`: FDs %d/%d, EMFILE lines %d.',
                standby.name, pod, fdMax, fdLim, emfile,
            )
            var = node.RunOnHost("df -P /var | awk 'NR == 2 {print $5}'")
            varPct = var.stdout.strip().rstrip('%') if var.success else ''
            if (varPct.isdigit() and (int(varPct) > pars.varMaxPct)):
                logs.warning('Node `%s` `/var` is almost full.', nodeName)
                break
            if (fdLim and (fdMax >= fdLim)):
                limAt = limAt or time.monotonic()
                if (emfile or (time.monotonic() - limAt >= pars.emfileWait)):
                    break
        assert podSeen, (
            f'Prerequisites not met: replacement MDS `{standby.name}` Pod '
            'never ran (no schedulable storage Node in a free zone?).'
        )
        assert (fdFirst is not None), (
            f'Prerequisites not met: FD count of MDS `{standby.name}` was '
            'never read.'
        )
        if (fdMax - fdFirst > pars.fdGrowMax):
            findings.append(
                f'MDS `{standby.name}` FD leak: {fdFirst} -> {fdMax} '
                f'(limit {fdLim})'
            )
        if emfile:
            findings.append(
                f'MDS `{standby.name}` logged {emfile} EMFILE lines'
            )

        # Wait for the kubelet timer, then let the cluster settle.
        if ((remTO := fxtMDS.cln.upAt - time.monotonic()) > 0):
            logs.info('Waiting %d sec. for kubelet.', remTO)
            time.sleep(remTO)
        assert stbyNode.WaitReady(), (
            f'Node `{stbyNode.name}` is not Ready after the kubelet timer.'
        )
        time.sleep(pars.settleSec)

        # Failover: the standby-replay MDS must take over the active rank.
        ceph.FailMDS(fxtMDS.active.name)
        deadline = time.monotonic() + pars.failoverTO
        while True:
            time.sleep(pars.pollInt)
            try:
                (_, checks) = ceph.Health()
                states = ceph.MDSstates().values()
            except RuntimeError as error:
                logs.warning('Could not query Ceph: %s', error)
                (checks, states) = ({'UNKNOWN'}, [])
            if (('FS_DEGRADED' not in checks) and ('up:active' in states)):
                break
            if (time.monotonic() >= deadline):
                findings.append(
                    f'CephFS still degraded {pars.failoverTO} sec. after '
                    f'failing MDS `{fxtMDS.active.name}` (checks: '
                    f'{sorted(checks)})'
                )
                break

        assert not findings, '; '.join(findings)
