import dataclasses
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.ODF as MUT
import Utils


SOURCE_DEFAULT_CONFIG = MUT._DEF__FXT_CFG


@pytest.fixture(name='testBoundaries', autouse=True)
def TestBoundaries(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    namedDict = mock.Mock(
        side_effect=lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    cephFactory = mock.Mock()
    ceph = cephFactory.return_value
    ceph.EnableTools.return_value = True
    ceph.Health.return_value = ('HEALTH_OK', set())
    sysModule = SimpleNamespace(
        exc_info=mock.Mock(return_value=(None, None, None)),
    )
    cephCfg = SimpleNamespace(
        chkHealth=True,
        timeout=SimpleNamespace(toolsReady='7m'),
    )
    final = SimpleNamespace(
        kCfg='/synthetic/config',
        cliCfg=SimpleNamespace(ocCfg={}),
        ns='synthetic-ns',
        fxtInf=SimpleNamespace(cephCfg=cephCfg),
        testPars=SimpleNamespace(),
    )
    defaults = mock.MagicMock()
    defaults.__or__.return_value = final
    logs = mock.Mock()
    clock = Utils.FakeClock()
    timeModule = SimpleNamespace(
        monotonic=mock.Mock(side_effect=clock.Now),
        sleep=mock.Mock(side_effect=clock.Sleep),
    )
    mdsFactory = mock.Mock(side_effect=lambda name, ceph: SimpleNamespace(
        name=name, ceph=ceph, Pod=mock.Mock(return_value=('pod', 'node')),
    ))
    nodeFactory = mock.Mock()
    monkeypatch.setattr(MUT, 'NamedDict', namedDict)
    monkeypatch.setattr(MUT, 'Ceph', cephFactory)
    monkeypatch.setattr(MUT, 'sys', sysModule)
    monkeypatch.setattr(MUT, 'logs', logs)
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', defaults)
    monkeypatch.setattr(MUT, 'time', timeModule)
    monkeypatch.setattr(MUT, 'MDS', mdsFactory)
    monkeypatch.setattr(MUT, 'Node', nodeFactory)
    return SimpleNamespace(
        namedDict=namedDict, cephFactory=cephFactory, ceph=ceph,
        sysModule=sysModule, cephCfg=cephCfg, final=final,
        defaults=defaults, logs=logs, clock=clock, timeModule=timeModule,
        mdsFactory=mdsFactory, nodeFactory=nodeFactory,
    )


def test__DEF__FXT_CFG_UsesNestedCephConfiguration() -> None:
    cfg = SOURCE_DEFAULT_CONFIG

    assert isinstance(cfg.kCfg, str)
    assert (cfg.cliCfg.ocCfg == {})
    assert (cfg.ns == 'openshift-storage')
    assert (cfg.fxtInf.cephCfg.chkHealth is True)
    assert (cfg.fxtInf.cephCfg.timeout == {
        'toolsReady': '10m', 'healthOK': 1800,
    })
    assert (cfg.testPars == {})


@pytest.mark.parametrize('field', ['ceph', 'fxtInf', 'testPars', 'cli', 'ns'])
def test_FxtODF_YieldsFrozenObjectAndRestoresToolbox(
    testBoundaries: SimpleNamespace,
    field: str,
) -> None:
    testBoundaries.final.cliCfg.ocCfg = {
        'context': 'oc-context', 'kubeconfig': '/ignored',
    }
    request = SimpleNamespace(param={'ns': 'custom-ns'})
    ocFactory = mock.Mock()
    generator = MUT.FxtODF.__wrapped__(request, ocFactory)
    try:
        context = next(generator)
        assert isinstance(context, MUT.FxtObjODF)
        assert (
            {item.name for item in dataclasses.fields(context)} ==
            {'ceph', 'fxtInf', 'testPars', 'cli', 'ns'}
        )
        assert (context.ceph is testBoundaries.ceph)
        assert (context.fxtInf is testBoundaries.final.fxtInf)
        assert (context.testPars is testBoundaries.final.testPars)
        assert (context.cli.oc is ocFactory.return_value)
        assert (context.ns == 'synthetic-ns')
        testBoundaries.namedDict.assert_any_call(ns='custom-ns')
        testBoundaries.defaults.__or__.assert_called_once()
        ocFactory.assert_called_once_with(
            ns='synthetic-ns', context='oc-context',
            kubeconfig='/synthetic/config',
        )
        testBoundaries.cephFactory.assert_called_once_with(
            'synthetic-ns', oc=ocFactory.return_value,
        )
        testBoundaries.ceph.EnableTools.assert_called_once_with(timeout='7m')
        testBoundaries.ceph.Health.assert_called_once_with()
        testBoundaries.ceph.DisableTools.assert_not_called()
        original = getattr(context, field)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(context, field, object())
        assert (getattr(context, field) is original)
        with pytest.raises(StopIteration):
            next(generator)
        testBoundaries.ceph.DisableTools.assert_called_once_with(True)
        testBoundaries.logs.info.assert_called_once_with(
            'The `fxtODF` fixture teardown complete.',
        )
    finally:
        generator.close()


@pytest.mark.parametrize('hasRequestParam', [False, True])
def test_FxtODF_UsesDefaultConfigurationWithAbsentOrEmptyRequestParam(
    testBoundaries: SimpleNamespace,
    hasRequestParam: bool,
) -> None:
    request = SimpleNamespace(param={}) if hasRequestParam else (
        SimpleNamespace()
    )
    ocFactory = mock.Mock()
    generator = MUT.FxtODF.__wrapped__(request, ocFactory)
    try:
        next(generator)
        assert (testBoundaries.namedDict.call_args_list == [
            mock.call(), mock.call(oc=ocFactory.return_value),
        ])
        ocFactory.assert_called_once_with(
            ns='synthetic-ns', kubeconfig='/synthetic/config',
        )
    finally:
        generator.close()


def test_FxtODF_SkipsHealthCheckWhenDisabled(
    testBoundaries: SimpleNamespace,
) -> None:
    testBoundaries.cephCfg.chkHealth = False
    testBoundaries.ceph.Health.return_value = ('HEALTH_ERR', {'X'})
    generator = MUT.FxtODF.__wrapped__(SimpleNamespace(), mock.Mock())
    try:
        assert (next(generator).ceph is testBoundaries.ceph)
        testBoundaries.ceph.Health.assert_not_called()
    finally:
        generator.close()


@pytest.mark.parametrize('toolsOn', [False, True])
def test_FxtODF_RaisesWhenUnhealthyAndRestoresToolbox(
    testBoundaries: SimpleNamespace,
    toolsOn: bool,
) -> None:
    testBoundaries.ceph.EnableTools.return_value = toolsOn
    testBoundaries.ceph.Health.return_value = (
        'HEALTH_WARN', {'MDS_SLOW_METADATA_IO', 'FS_DEGRADED'},
    )
    error = RuntimeError('primary')
    testBoundaries.sysModule.exc_info.return_value = (
        RuntimeError, error, None,
    )
    generator = MUT.FxtODF.__wrapped__(SimpleNamespace(), mock.Mock())

    with pytest.raises(
        RuntimeError,
        match=(
            r"Ceph is not `HEALTH_OK`: `HEALTH_WARN` "
            r"\['FS_DEGRADED', 'MDS_SLOW_METADATA_IO'\]."
        ),
    ): next(generator)
    testBoundaries.ceph.DisableTools.assert_called_once_with(toolsOn)


def test_FxtODF_RaisesWhenToolboxUnavailable(
    testBoundaries: SimpleNamespace,
) -> None:
    error = RuntimeError('toolbox unavailable')
    testBoundaries.ceph.EnableTools.side_effect = error
    testBoundaries.sysModule.exc_info.return_value = (
        RuntimeError, error, None,
    )
    generator = MUT.FxtODF.__wrapped__(SimpleNamespace(), mock.Mock())

    with pytest.raises(RuntimeError) as raised: next(generator)
    assert (raised.value is error)
    testBoundaries.ceph.Health.assert_not_called()
    testBoundaries.ceph.DisableTools.assert_called_once_with(False)


def test_FxtODF_RaisesTeardownErrorWithoutPrimaryException(
    testBoundaries: SimpleNamespace,
) -> None:
    error = RuntimeError('patch failed')
    testBoundaries.ceph.DisableTools.side_effect = error
    generator = MUT.FxtODF.__wrapped__(SimpleNamespace(), mock.Mock())
    next(generator)

    with pytest.raises(RuntimeError) as raised: next(generator)
    assert (raised.value is error)
    testBoundaries.logs.error.assert_called_once_with(
        'Could not disable Ceph toolbox: %s', error,
    )
    testBoundaries.logs.info.assert_not_called()


def test_FxtODF_PreservesPrimaryExceptionOverTeardownError(
    testBoundaries: SimpleNamespace,
) -> None:
    testBoundaries.ceph.DisableTools.side_effect = RuntimeError('patch')
    primary = AssertionError('test failed')
    testBoundaries.sysModule.exc_info.return_value = (
        AssertionError, primary, None,
    )
    generator = MUT.FxtODF.__wrapped__(SimpleNamespace(), mock.Mock())
    next(generator)

    with pytest.raises(AssertionError) as raised: generator.throw(primary)
    assert (raised.value is primary)
    testBoundaries.logs.error.assert_called_once()
    testBoundaries.logs.info.assert_called_once_with(
        'The `fxtODF` fixture teardown complete.',
    )


def MDSodfObject() -> SimpleNamespace:
    ceph = mock.Mock()
    ceph.MDSstates.return_value = {
        'fs-a': 'up:active', 'fs-b': 'up:standby-replay',
    }
    ceph.Health.return_value = ('HEALTH_OK', set())
    oc = mock.Mock()
    oc.Run.return_value = SimpleNamespace(success=True)
    return SimpleNamespace(
        ceph=ceph,
        cli=SimpleNamespace(oc=oc),
        ns='odf-ns',
        fxtInf=SimpleNamespace(cephCfg=SimpleNamespace(
            timeout=SimpleNamespace(healthOK=90),
        )),
    )


def MDSpods(
    testBoundaries: SimpleNamespace,
    pods: dict[str, tuple[str, str]],
) -> None:
    def Make(name: str, ceph: object) -> SimpleNamespace:
        return SimpleNamespace(
            name=name, ceph=ceph, Pod=mock.Mock(return_value=pods[name]),
        )
    testBoundaries.mdsFactory.side_effect = Make


def test_MDSclnInf_DefaultsToKubeletNotStopped() -> None:
    assert (MUT.MDSclnInf().upAt is None)
    cln = MUT.MDSclnInf()
    cln.upAt = 5.0
    assert (cln.upAt == 5.0)


@pytest.mark.parametrize('field', ['active', 'standby', 'stbyNode', 'cln'])
def test_FxtMDS_YieldsRolesAndSkipsCleanupWithoutKubeletStop(
    testBoundaries: SimpleNamespace,
    field: str,
) -> None:
    fxtODF = MDSodfObject()
    MDSpods(testBoundaries, {
        'fs-a': ('mds-a-pod', 'worker-a'),
        'fs-b': ('mds-b-pod', 'worker-b'),
    })
    generator = MUT.FxtMDS.__wrapped__(fxtODF)
    try:
        context = next(generator)
        assert isinstance(context, MUT.FxtObjMDS)
        assert (context.active.name == 'fs-a')
        assert (context.standby.name == 'fs-b')
        assert (context.active.ceph is fxtODF.ceph)
        assert (context.stbyNode is testBoundaries.nodeFactory.return_value)
        testBoundaries.nodeFactory.assert_called_once_with(
            'worker-b', oc=fxtODF.cli.oc,
        )
        assert (context.cln.upAt is None)
        original = getattr(context, field)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(context, field, object())
        assert (getattr(context, field) is original)
        with pytest.raises(StopIteration):
            next(generator)
        context.stbyNode.WaitReady.assert_not_called()
        fxtODF.cli.oc.Run.assert_not_called()
        fxtODF.ceph.Health.assert_not_called()
        testBoundaries.timeModule.sleep.assert_not_called()
        testBoundaries.logs.info.assert_called_once_with(
            'The `fxtMDS` fixture teardown complete.',
        )
    finally:
        generator.close()


@pytest.mark.parametrize('states', [
    {},
    {'fs-a': 'up:active'},
    {'fs-b': 'up:standby-replay'},
    {'fs-a': 'up:active', 'fs-b': 'up:standby'},
    {
        'fs-a': 'up:active', 'fs-b': 'up:standby-replay',
        'fs-c': 'up:active',
    },
    {
        'fs-a': 'up:active', 'fs-b': 'up:standby-replay',
        'fs-c': 'up:standby-replay',
    },
])
def test_FxtMDS_RaisesWithoutOneActiveAndOneStandbyReplay(
    testBoundaries: SimpleNamespace,
    states: dict[str, str],
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.ceph.MDSstates.return_value = states

    with pytest.raises(
        RuntimeError,
        match=(
            'Prerequisites not met: expected one active and one '
            'standby-replay MDS'
        ),
    ): next(MUT.FxtMDS.__wrapped__(fxtODF))
    testBoundaries.mdsFactory.assert_not_called()


@pytest.mark.parametrize('healthOK', [0, -1, 1.5, '30m', True, None])
def test_FxtMDS_RejectsInvalidHealthTimeoutBeforeAnyAction(
    testBoundaries: SimpleNamespace,
    healthOK: object,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.fxtInf.cephCfg.timeout.healthOK = healthOK

    with pytest.raises(
        ValueError, match=r'The `healthOK` must be a positive integer',
    ): next(MUT.FxtMDS.__wrapped__(fxtODF))
    fxtODF.ceph.MDSstates.assert_not_called()
    testBoundaries.mdsFactory.assert_not_called()


@pytest.mark.parametrize('missing', ['fs-a', 'fs-b'])
def test_FxtMDS_RaisesWithoutRunningPod(
    testBoundaries: SimpleNamespace,
    missing: str,
) -> None:
    fxtODF = MDSodfObject()
    pods = {'fs-a': ('mds-a-pod', 'worker-a'), 'fs-b': ('mds-b-pod', 'w-b')}
    pods[missing] = ('', '')
    MDSpods(testBoundaries, pods)

    with pytest.raises(
        RuntimeError,
        match=f'Prerequisites not met: no Running Pod for MDS `{missing}`.',
    ): next(MUT.FxtMDS.__wrapped__(fxtODF))
    testBoundaries.nodeFactory.assert_not_called()


@pytest.mark.parametrize('remSec', [-5.0, 0.0, 120.0])
def test_FxtMDS_RecoversAfterKubeletStop(
    testBoundaries: SimpleNamespace,
    remSec: float,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.ceph.Health.side_effect = [
        RuntimeError('toolbox moving'),
        ('HEALTH_WARN', {'MDS_SLOW_METADATA_IO'}),
        ('HEALTH_OK', set()),
    ]
    generator = MUT.FxtMDS.__wrapped__(fxtODF)
    context = next(generator)
    context.stbyNode.name = 'worker-b'
    context.stbyNode.WaitReady.return_value = True
    context.cln.upAt = testBoundaries.clock.now + remSec

    with pytest.raises(StopIteration): next(generator)
    sleeps = [
        call.args[0]
        for call in testBoundaries.timeModule.sleep.call_args_list
    ]
    assert (sleeps == ([remSec] if (remSec > 0) else []) + [30, 30])
    if (remSec > 0):
        testBoundaries.logs.info.assert_any_call(
            'Waiting %d sec. for kubelet on Node `%s`.', remSec, 'worker-b',
        )
    context.stbyNode.WaitReady.assert_called_once_with()
    fxtODF.cli.oc.Run.assert_called_once_with(
        'delete',
        'Pod',
        n='odf-ns',
        l='app=rook-ceph-mds,ceph_daemon_id=fs-b',
        ignore_not_found=True, timeout='5m', wait='true',
    )
    assert (fxtODF.ceph.Health.call_count == 3)
    testBoundaries.logs.error.assert_not_called()


def test_FxtMDS_RaisesCleanupFailuresWithoutPrimaryException(
    testBoundaries: SimpleNamespace,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.cli.oc.Run.return_value = SimpleNamespace(success=False)
    fxtODF.ceph.Health.return_value = ('HEALTH_WARN', {'X'})
    generator = MUT.FxtMDS.__wrapped__(fxtODF)
    context = next(generator)
    context.stbyNode.name = 'worker-b'
    context.stbyNode.WaitReady.return_value = False
    context.cln.upAt = testBoundaries.clock.now

    with pytest.raises(RuntimeError) as raised: next(generator)
    assert (str(raised.value) == (
        'MDS cleanup failed: Node `worker-b` is not Ready; '
        'Could not delete MDS `fs-b` Pods; '
        'Ceph is not `HEALTH_OK` after 90 sec.'
    ))
    assert (fxtODF.ceph.Health.call_count == 4)
    testBoundaries.logs.info.assert_not_called()


def test_FxtMDS_PreservesPrimaryExceptionOverCleanupFailures(
    testBoundaries: SimpleNamespace,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.ceph.Health.return_value = ('HEALTH_WARN', {'X'})
    primary = AssertionError('test failed')
    testBoundaries.sysModule.exc_info.return_value = (
        AssertionError, primary, None,
    )
    generator = MUT.FxtMDS.__wrapped__(fxtODF)
    context = next(generator)
    context.stbyNode.WaitReady.return_value = True
    context.cln.upAt = testBoundaries.clock.now

    with pytest.raises(AssertionError) as raised: generator.throw(primary)
    assert (raised.value is primary)
    testBoundaries.logs.error.assert_called_once_with(
        '%s; preserving primary exception.',
        'MDS cleanup failed: Ceph is not `HEALTH_OK` after 90 sec.',
    )
    testBoundaries.logs.info.assert_called_once_with(
        'The `fxtMDS` fixture teardown complete.',
    )


def test_FxtMDS_LimitsHealthWaitToBudget(
    testBoundaries: SimpleNamespace,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.fxtInf.cephCfg.timeout.healthOK = 45
    fxtODF.ceph.Health.return_value = ('HEALTH_WARN', {'X'})
    generator = MUT.FxtMDS.__wrapped__(fxtODF)
    context = next(generator)
    context.stbyNode.WaitReady.return_value = True
    context.cln.upAt = testBoundaries.clock.now

    with pytest.raises(
        RuntimeError, match='Ceph is not `HEALTH_OK` after 45 sec.',
    ): next(generator)
    assert ([
        call.args[0]
        for call in testBoundaries.timeModule.sleep.call_args_list
    ] == [30, 15])
    assert (fxtODF.ceph.Health.call_count == 3)
