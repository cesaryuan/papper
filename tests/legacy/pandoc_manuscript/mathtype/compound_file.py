"""Minimal OLE compound-file reader used to verify MathType output."""

import struct
from dataclasses import dataclass


END_OF_CHAIN = 0xFFFFFFFE
FREE_SECTOR = 0xFFFFFFFF
FAT_SECTOR = 0xFFFFFFFD
NO_STREAM = 0xFFFFFFFF


@dataclass
class DirectoryEntry:
    """A single CFB directory entry needed for stream extraction."""

    name: str
    object_type: int
    start_sector: int
    size: int


class CompoundFile:
    """Minimal CFB reader for verifying MathType OLE streams."""

    def __init__(self, data: bytes):
        if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError("not an OLE compound file")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
        self.mini_sector_size = 1 << struct.unpack_from("<H", data, 32)[0]
        self.first_dir_sector = struct.unpack_from("<I", data, 48)[0]
        self.mini_cutoff = struct.unpack_from("<I", data, 56)[0]
        self.first_minifat_sector = struct.unpack_from("<I", data, 60)[0]
        self.num_minifat_sectors = struct.unpack_from("<I", data, 64)[0]
        self.fat = self._read_fat()
        self.entries = self._read_directory()
        self.root = next((entry for entry in self.entries if entry.object_type == 5), None)
        self.mini_fat = self._read_minifat()
        self.root_mini_stream = self._read_regular_stream(self.root) if self.root else b""

    def _sector(self, sector_id: int) -> bytes:
        """Return one regular sector by id."""
        offset = (sector_id + 1) * self.sector_size
        return self.data[offset : offset + self.sector_size]

    def _chain(self, start_sector: int, fat: list[int] | None = None) -> list[int]:
        """Return the sector chain beginning at start_sector."""
        table = self.fat if fat is None else fat
        chain: list[int] = []
        sector = start_sector
        while sector not in {END_OF_CHAIN, FREE_SECTOR, NO_STREAM}:
            if sector >= len(table):
                raise ValueError(f"sector chain points outside FAT: {sector}")
            chain.append(sector)
            sector = table[sector]
        return chain

    def _read_fat(self) -> list[int]:
        """Read the first-level FAT sectors from the compound-file header."""
        difat = [
            value
            for value in struct.unpack_from("<109I", self.data, 76)
            if value not in {FREE_SECTOR, END_OF_CHAIN}
        ]
        sectors: list[int] = []
        for fat_sector in difat:
            if fat_sector == FAT_SECTOR:
                continue
            sectors.extend(struct.unpack("<" + "I" * (self.sector_size // 4), self._sector(fat_sector)))
        return sectors

    def _read_directory(self) -> list[DirectoryEntry]:
        """Read directory entries from the regular directory stream."""
        raw = b"".join(self._sector(sector) for sector in self._chain(self.first_dir_sector))
        entries: list[DirectoryEntry] = []
        for offset in range(0, len(raw), 128):
            chunk = raw[offset : offset + 128]
            name_len = struct.unpack_from("<H", chunk, 64)[0]
            if name_len < 2:
                continue
            name = chunk[: name_len - 2].decode("utf-16le", errors="replace")
            entries.append(
                DirectoryEntry(
                    name=name,
                    object_type=chunk[66],
                    start_sector=struct.unpack_from("<I", chunk, 116)[0],
                    size=struct.unpack_from("<Q", chunk, 120)[0],
                )
            )
        return entries

    def _read_minifat(self) -> list[int]:
        """Read the MiniFAT used for streams below the mini-stream cutoff."""
        if self.first_minifat_sector in {END_OF_CHAIN, FREE_SECTOR, NO_STREAM}:
            return []
        raw = b"".join(self._sector(sector) for sector in self._chain(self.first_minifat_sector))
        return list(struct.unpack("<" + "I" * (len(raw) // 4), raw))

    def _read_regular_stream(self, entry: DirectoryEntry | None) -> bytes:
        """Read a stream stored in regular sectors."""
        if entry is None or entry.start_sector in {END_OF_CHAIN, FREE_SECTOR, NO_STREAM}:
            return b""
        raw = b"".join(self._sector(sector) for sector in self._chain(entry.start_sector))
        return raw[: entry.size]

    def read_stream(self, name: str) -> bytes:
        """Read a named stream from regular or mini-stream storage."""
        entry = next((item for item in self.entries if item.name == name), None)
        if entry is None:
            raise KeyError(name)
        if entry.size >= self.mini_cutoff or not self.mini_fat:
            return self._read_regular_stream(entry)

        parts = []
        sector = entry.start_sector
        while sector not in {END_OF_CHAIN, FREE_SECTOR, NO_STREAM}:
            offset = sector * self.mini_sector_size
            parts.append(self.root_mini_stream[offset : offset + self.mini_sector_size])
            if sector >= len(self.mini_fat):
                raise ValueError(f"mini sector chain points outside MiniFAT: {sector}")
            sector = self.mini_fat[sector]
        return b"".join(parts)[: entry.size]
