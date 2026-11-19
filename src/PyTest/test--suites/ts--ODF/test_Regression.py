import logging
import math
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
                settleSec=150,      # Min. settle time after kubelet is back.
                rolesTO=600,        # Max. wait for the MDS roles to return.
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
        Descriptor (FD) leak and `EMFILE`. Once the MDS roles are back, fail
        the active MDS and check that the standby-replay MDS takes over.
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
        # is logged, or until the kubelet is back. FDs are tracked per
        # process (PID): a restarted process starts a new count. Each log
        # read covers the time since the last successful read.
        (fdTrk, fdLim, emfile, logAt, limAt) = ({}, 0, 0, None, None)
        podSeen = False
        while (time.monotonic() < fxtMDS.cln.upAt):
            time.sleep(pars.pollInt)
            (pod, nodeName) = standby.Pod(exclNode=stbyNode.name)
            if not pod: continue
            podSeen = True
            node = Node(nodeName, oc=oc)
            if (fdStat := standby.FDstat(node)) is not None:
                (pid, fdCount, fdLim) = fdStat
                (fdFirst, fdMax) = fdTrk.get(pid, (fdCount, fdCount))
                fdTrk[pid] = (fdFirst, max(fdMax, fdCount))
            readAt = time.monotonic()
            sinceSec = (
                pars.pollInt if (logAt is None)
                else math.ceil(readAt - logAt)
            )
            count = standby.LogCount(
                pod,
                'too many open files|EMFILE',
                since=f'{sinceSec}s',
            )
            if (count >= 0): (emfile, logAt) = (emfile + count, readAt)
            fdPeak = max((fdMax for (_, fdMax) in fdTrk.values()), default=0)
            logs.info(
                'MDS `%s` Pod `%s`: FDs %d/%d, EMFILE lines %d.',
                standby.name, pod, fdPeak, fdLim, emfile,
            )
            var = node.RunOnHost("df -P /var | awk 'NR == 2 {print $5}'")
            varPct = var.stdout.strip().rstrip('%') if var.success else ''
            if (varPct.isdigit() and (int(varPct) > pars.varMaxPct)):
                logs.warning('Node `%s` `/var` is almost full.', nodeName)
                break
            if (fdLim and (fdPeak >= fdLim)):
                limAt = limAt or time.monotonic()
                if (emfile or (time.monotonic() - limAt >= pars.emfileWait)):
                    break
        assert podSeen, (
            f'Prerequisites not met: replacement MDS `{standby.name}` Pod '
            'never ran (no schedulable storage Node in a free zone?).'
        )
        assert fdTrk, (
            f'Prerequisites not met: FD count of MDS `{standby.name}` was '
            'never read.'
        )
        assert (logAt is not None), (
            f'Prerequisites not met: log of MDS `{standby.name}` was never '
            'read.'
        )
        for (pid, (fdFirst, fdMax)) in fdTrk.items():
            if (fdMax - fdFirst > pars.fdGrowMax):
                findings.append(
                    f'MDS `{standby.name}` (PID {pid}) FD leak: {fdFirst} -> '
                    f'{fdMax} (limit {fdLim})'
                )
        if emfile:
            findings.append(
                f'MDS `{standby.name}` logged {emfile} EMFILE lines'
            )

        # Wait for the kubelet timer and the Node, then for the MDS roles: one
        # active MDS, and the standby-replay MDS back in its role.
        if ((remTO := fxtMDS.cln.upAt - time.monotonic()) > 0):
            logs.info('Waiting %d sec. for kubelet.', remTO)
            time.sleep(remTO)
        active = ''
        if not stbyNode.WaitReady():
            findings.append(
                f'Node `{stbyNode.name}` is not Ready after the kubelet timer'
            )
        else:
            time.sleep(pars.settleSec)
            deadline = time.monotonic() + pars.rolesTO
            while True:
                try: states = ceph.MDSstates()
                except RuntimeError as error:
                    logs.warning('Could not query Ceph: %s', error)
                    states = {}
                actives = [
                    name for (name, state) in states.items()
                    if (state == 'up:active')
                ]
                if (
                    (states.get(standby.name) == 'up:standby-replay') and
                    (len(actives) == 1)
                ):
                    active = actives[0]
                    break
                if (time.monotonic() >= deadline):
                    findings.append(
                        f'MDS `{standby.name}` is not back as standby-replay '
                        f'{pars.rolesTO} sec. after the kubelet (states: '
                        f'{states})'
                    )
                    break
                time.sleep(pars.pollInt)

        # Failover: the standby-replay MDS must take over the active rank.
        if active:
            ceph.FailMDS(active)
            deadline = time.monotonic() + pars.failoverTO
            while True:
                time.sleep(pars.pollInt)
                try:
                    (_, checks) = ceph.Health()
                    state = ceph.MDSstates().get(standby.name, '')
                except RuntimeError as error:
                    logs.warning('Could not query Ceph: %s', error)
                    (checks, state) = ({'UNKNOWN'}, '')
                if (('FS_DEGRADED' not in checks) and (state == 'up:active')):
                    break
                if (time.monotonic() >= deadline):
                    findings.append(
                        f'MDS `{standby.name}` did not take over '
                        f'{pars.failoverTO} sec. after failing MDS '
                        f'`{active}` (state: `{state}`, checks: '
                        f'{sorted(checks)})'
                    )
                    break

        assert not findings, '; '.join(findings)
