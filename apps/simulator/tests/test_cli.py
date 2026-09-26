from typer.testing import CliRunner

from shelfsense_simulator.cli import NOT_IMPLEMENTED_EXIT_CODE, app

runner = CliRunner()


def test_run_refuses_loudly_until_p5() -> None:
    result = runner.invoke(app, ["run", "--broker-url", "mqtt://example:1883"])
    assert result.exit_code == NOT_IMPLEMENTED_EXIT_CODE
    assert "arrives in P5" in result.output
    assert "mqtt://example:1883" in result.output


def test_no_args_prints_help() -> None:
    result = runner.invoke(app, [])
    assert "Publish synthetic" in result.output
