"""Common helpers for the Backup integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.backup import (
    DOMAIN,
    AddonInfo,
    AgentBackup,
    BackupAgent,
    BackupAgentPlatformProtocol,
    BackupNotFound,
    Folder,
)
from homeassistant.components.backup.const import DATA_MANAGER
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_setup_component

from tests.common import MockPlatform, mock_platform

LOCAL_AGENT_ID = f"{DOMAIN}.local"

TEST_BACKUP_ABC123 = AgentBackup(
    addons=[AddonInfo(name="Test", slug="test", version="1.0.0")],
    backup_id="abc123",
    database_included=True,
    date="1970-01-01T00:00:00.000Z",
    extra_metadata={"instance_id": "our_uuid", "with_automatic_settings": True},
    folders=[Folder.MEDIA, Folder.SHARE],
    homeassistant_included=True,
    homeassistant_version="2024.12.0",
    name="Test",
    protected=False,
    size=0,
)
TEST_BACKUP_PATH_ABC123 = Path("abc123.tar")

TEST_BACKUP_DEF456 = AgentBackup(
    addons=[],
    backup_id="def456",
    database_included=False,
    date="1980-01-01T00:00:00.000Z",
    extra_metadata={"instance_id": "unknown_uuid", "with_automatic_settings": True},
    folders=[Folder.MEDIA, Folder.SHARE],
    homeassistant_included=True,
    homeassistant_version="2024.12.0",
    name="Test 2",
    protected=False,
    size=1,
)
TEST_BACKUP_PATH_DEF456 = Path("custom_def456.tar")

TEST_DOMAIN = "test"


async def aiter_from_iter(iterable: Iterable) -> AsyncIterator:
    """Convert an iterable to an async iterator."""
    for i in iterable:
        yield i


class BackupAgentTest(BackupAgent):
    """Test backup agent."""

    domain = "test"

    def __init__(self, name: str, backups: list[AgentBackup] | None = None) -> None:
        """Initialize the backup agent."""
        self.name = name
        self.unique_id = name
        if backups is None:
            backups = [
                AgentBackup(
                    addons=[AddonInfo(name="Test", slug="test", version="1.0.0")],
                    backup_id="abc123",
                    database_included=True,
                    date="1970-01-01T00:00:00Z",
                    extra_metadata={},
                    folders=[Folder.MEDIA, Folder.SHARE],
                    homeassistant_included=True,
                    homeassistant_version="2024.12.0",
                    name="Test",
                    protected=False,
                    size=13,
                )
            ]

        self._backup_data: bytearray | None = None
        self._backups = {backup.backup_id: backup for backup in backups}

    async def async_download_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> AsyncIterator[bytes]:
        """Download a backup file."""
        return AsyncMock(spec_set=["__aiter__"])

    async def async_upload_backup(
        self,
        *,
        open_stream: Callable[[], Coroutine[Any, Any, AsyncIterator[bytes]]],
        backup: AgentBackup,
        **kwargs: Any,
    ) -> None:
        """Upload a backup."""
        self._backups[backup.backup_id] = backup
        backup_stream = await open_stream()
        self._backup_data = bytearray()
        async for chunk in backup_stream:
            self._backup_data += chunk

    async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
        """List backups."""
        return list(self._backups.values())

    async def async_get_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> AgentBackup | None:
        """Return a backup."""
        return self._backups.get(backup_id)

    async def async_delete_backup(
        self,
        backup_id: str,
        **kwargs: Any,
    ) -> None:
        """Delete a backup file."""


def mock_backup_agent(name: str, backups: list[AgentBackup] | None = None) -> Mock:
    """Create a mock backup agent."""

    async def get_backup(backup_id: str, **kwargs: Any) -> AgentBackup | None:
        """Get a backup."""
        return next((b for b in backups if b.backup_id == backup_id), None)

    backups = backups or []
    mock_agent = Mock(spec=BackupAgent)
    mock_agent.domain = "test"
    mock_agent.name = name
    mock_agent.unique_id = name
    type(mock_agent).agent_id = BackupAgent.agent_id
    mock_agent.async_delete_backup = AsyncMock(
        spec_set=[BackupAgent.async_delete_backup]
    )
    mock_agent.async_download_backup = AsyncMock(
        side_effect=BackupNotFound, spec_set=[BackupAgent.async_download_backup]
    )
    mock_agent.async_get_backup = AsyncMock(
        side_effect=get_backup, spec_set=[BackupAgent.async_get_backup]
    )
    mock_agent.async_list_backups = AsyncMock(
        return_value=backups, spec_set=[BackupAgent.async_list_backups]
    )
    mock_agent.async_upload_backup = AsyncMock(
        spec_set=[BackupAgent.async_upload_backup]
    )
    return mock_agent


async def setup_backup_integration(
    hass: HomeAssistant,
    with_hassio: bool = False,
    configuration: ConfigType | None = None,
    *,
    backups: dict[str, list[AgentBackup]] | None = None,
    remote_agents: list[str] | None = None,
) -> bool:
    """Set up the Backup integration."""
    with (
        patch("homeassistant.components.backup.is_hassio", return_value=with_hassio),
        patch(
            "homeassistant.components.backup.backup.is_hassio", return_value=with_hassio
        ),
    ):
        remote_agents = remote_agents or []
        platform = Mock(
            async_get_backup_agents=AsyncMock(
                return_value=[BackupAgentTest(agent, []) for agent in remote_agents]
            ),
            spec_set=BackupAgentPlatformProtocol,
        )

        mock_platform(hass, f"{TEST_DOMAIN}.backup", platform or MockPlatform())
        assert await async_setup_component(hass, TEST_DOMAIN, {})

        result = await async_setup_component(hass, DOMAIN, configuration or {})
        await hass.async_block_till_done()
        if not backups:
            return result

        for agent_id, agent_backups in backups.items():
            if with_hassio and agent_id == LOCAL_AGENT_ID:
                continue
            agent = hass.data[DATA_MANAGER].backup_agents[agent_id]

            async def open_stream() -> AsyncIterator[bytes]:
                """Open a stream."""
                return aiter_from_iter((b"backup data",))

            for backup in agent_backups:
                await agent.async_upload_backup(open_stream=open_stream, backup=backup)
            if agent_id == LOCAL_AGENT_ID:
                agent._loaded_backups = True

        return result


async def setup_backup_platform(
    hass: HomeAssistant,
    *,
    domain: str,
    platform: Any,
) -> None:
    """Set up a mock domain."""
    mock_platform(hass, f"{domain}.backup", platform)
    assert await async_setup_component(hass, domain, {})
    await hass.async_block_till_done()
