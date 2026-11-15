import asyncio

import pytest

from evals.personas import SCENARIOS


@pytest.mark.parametrize("name,description,scenario", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_persona(name, description, scenario):
    asyncio.run(scenario())
