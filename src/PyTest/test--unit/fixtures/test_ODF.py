import dataclasses
from types import SimpleNamespace
from unittest import mock

import pytest

import fixtures.ODF as MUT


SOURCE_DEFAULT_CONFIG = MUT._DEF__FXT_CFG
SOURCE_DEFAULT_MDS_CONFIG = MUT._DEF__FXT_MDS_CFG


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
    mdsDefaults = mock.MagicMock()
    mdsDefaults.__or__.side_effect = lambda other: SimpleNamespace(
        fsName=getattr(other, 'fsName', ''),
    )
    logs = mock.Mock()
    mdsFactory = mock.Mock(side_effect=lambda name, ceph: SimpleNamespace(
        name=name, ceph=ceph, Pod=mock.Mock(return_value=('pod', 'node')),
    ))
    nodeFactory = mock.Mock()
    monkeypatch.setattr(MUT, 'NamedDict', namedDict)
    monkeypatch.setattr(MUT, 'Ceph', cephFactory)
    monkeypatch.setattr(MUT, 'sys', sysModule)
    monkeypatch.setattr(MUT, 'logs', logs)
    monkeypatch.setattr(MUT, '_DEF__FXT_CFG', defaults)
    monkeypatch.setattr(MUT, '_DEF__FXT_MDS_CFG', mdsDefaults)
    monkeypatch.setattr(MUT, 'MDS', mdsFactory)
    monkeypatch.setattr(MUT, 'Node', nodeFactory)
    return SimpleNamespace(
        namedDict=namedDict, cephFactory=cephFactory, ceph=ceph,
        sysModule=sysModule, cephCfg=cephCfg, final=final,
        defaults=defaults, mdsDefaults=mdsDefaults, logs=logs,
        mdsFactory=mdsFactory, nodeFactory=nodeFactory,
    )


def test__DEF__FXT_CFG_UsesNestedCephConfiguration() -> None:
    cfg = SOURCE_DEFAULT_CONFIG

    assert isinstance(cfg.kCfg, str)
    assert (cfg.cliCfg.ocCfg == {})
    assert (cfg.ns == 'openshift-storage')
    assert (cfg.fxtInf.cephCfg.chkHealth is True)
    assert (cfg.fxtInf.cephCfg.timeout == {'toolsReady': '10m'})
    assert (cfg.testPars == {})


def test__DEF__FXT_MDS_CFG_DefaultsToTheODFfileSystem() -> None:
    assert (SOURCE_DEFAULT_MDS_CONFIG == {'fsName': ''})


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
    ceph.ODFfsName.return_value = 'fs'
    ceph.MDSstates.return_value = {
        'fs-a': 'up:active', 'fs-b': 'up:standby-replay',
    }
    return SimpleNamespace(ceph=ceph, cli=SimpleNamespace(oc=mock.Mock()))


def MDSpods(
    testBoundaries: SimpleNamespace,
    pods: dict[str, tuple[str, str]],
) -> None:
    def Make(name: str, ceph: object) -> SimpleNamespace:
        return SimpleNamespace(
            name=name, ceph=ceph, Pod=mock.Mock(return_value=pods[name]),
        )
    testBoundaries.mdsFactory.side_effect = Make


@pytest.mark.parametrize(
    'field', ['fsName', 'active', 'standby', 'activeNode', 'stbyNode'],
)
def test_FxtMDS_ReturnsFrozenRolesOfTheODFfileSystem(
    testBoundaries: SimpleNamespace,
    field: str,
) -> None:
    fxtODF = MDSodfObject()
    MDSpods(testBoundaries, {
        'fs-a': ('mds-a-pod', 'worker-a'),
        'fs-b': ('mds-b-pod', 'worker-b'),
    })
    nodes = {'worker-a': mock.Mock(), 'worker-b': mock.Mock()}
    testBoundaries.nodeFactory.side_effect = lambda name, oc: nodes[name]

    context = MUT.FxtMDS.__wrapped__(SimpleNamespace(), fxtODF)
    assert isinstance(context, MUT.FxtObjMDS)
    assert (
        {item.name for item in dataclasses.fields(context)} ==
        {'fsName', 'active', 'standby', 'activeNode', 'stbyNode'}
    )
    assert (context.fsName == 'fs')
    assert (context.active.name == 'fs-a')
    assert (context.standby.name == 'fs-b')
    assert (context.active.ceph is fxtODF.ceph)
    assert (context.activeNode is nodes['worker-a'])
    assert (context.stbyNode is nodes['worker-b'])
    assert (testBoundaries.nodeFactory.call_args_list == [
        mock.call('worker-a', oc=fxtODF.cli.oc),
        mock.call('worker-b', oc=fxtODF.cli.oc),
    ])
    fxtODF.ceph.ODFfsName.assert_called_once_with()
    fxtODF.ceph.MDSstates.assert_called_once_with('fs')
    testBoundaries.namedDict.assert_called_once_with()
    original = getattr(context, field)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(context, field, object())
    assert (getattr(context, field) is original)


def test_FxtMDS_UsesConfiguredFileSystem(
    testBoundaries: SimpleNamespace,
) -> None:
    fxtODF = MDSodfObject()
    request = SimpleNamespace(param={'fsName': 'fs2'})

    context = MUT.FxtMDS.__wrapped__(request, fxtODF)
    assert (context.fsName == 'fs2')
    testBoundaries.namedDict.assert_called_once_with(fsName='fs2')
    fxtODF.ceph.ODFfsName.assert_not_called()
    fxtODF.ceph.MDSstates.assert_called_once_with('fs2')


def test_FxtMDS_RaisesWithoutODFfileSystem(
    testBoundaries: SimpleNamespace,
) -> None:
    fxtODF = MDSodfObject()
    fxtODF.ceph.ODFfsName.side_effect = RuntimeError('no owned fs')

    with pytest.raises(RuntimeError) as raised:
        MUT.FxtMDS.__wrapped__(SimpleNamespace(), fxtODF)
    assert (str(raised.value) == 'Prerequisites not met: no owned fs')
    assert (raised.value.__cause__ is None)
    fxtODF.ceph.MDSstates.assert_not_called()


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

    with pytest.raises(RuntimeError) as raised:
        MUT.FxtMDS.__wrapped__(SimpleNamespace(), fxtODF)
    assert (str(raised.value) == (
        'Prerequisites not met: expected one active and one '
        f'standby-replay MDS in `fs`, got: {states}.'
    ))
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
    ): MUT.FxtMDS.__wrapped__(SimpleNamespace(), fxtODF)
    nodeCalls = 1 if (missing == 'fs-b') else 0
    assert (testBoundaries.nodeFactory.call_count == nodeCalls)
