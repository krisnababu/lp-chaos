import logging
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from fixtures.ODF import FxtObjMDS, FxtObjODF


logs = logging.getLogger()


class TestODF():
    '''
    Non-disruptive ODF Ceph fixture examples: read-only queries only, no fault
    injection.
    '''

    @pytest.mark.odf
    def test_CephHealth(self, fxtODF: 'FxtObjODF') -> None:
        '''Check Ceph `HEALTH_OK` via the fixture-enabled Ceph toolbox.'''
        (status, checks) = fxtODF.ceph.Health()
        logs.info('Ceph health: `%s` %s.', status, sorted(checks))
        assert (status == 'HEALTH_OK'), f'`{status}` {sorted(checks)}'

    @pytest.mark.odf
    def test_MDSdiscovery(
        self,
        fxtODF: 'FxtObjODF',
        fxtMDS: 'FxtObjMDS',
    ) -> None:
        '''Check the discovered MDS daemon roles, Pods, and Nodes.'''
        states = fxtODF.ceph.MDSstates(fxtMDS.fsName)
        logs.info('File system `%s` MDS states: %s.', fxtMDS.fsName, states)
        assert (states.get(fxtMDS.active.name) == 'up:active')
        assert (states.get(fxtMDS.standby.name) == 'up:standby-replay')
        for (role, mds, node) in (
            ('active', fxtMDS.active, fxtMDS.activeNode),
            ('standby-replay', fxtMDS.standby, fxtMDS.stbyNode),
        ):
            (pod, nodeName) = mds.Pod()
            logs.info(
                'The %s MDS `%s`: Pod `%s` on Node `%s`.',
                role, mds.name, pod, nodeName,
            )
            assert pod, f'No Running Pod for MDS `{mds.name}`.'
            assert (nodeName == node.name)
