/**
 * Reading files *and* folders out of a drop.
 *
 * A file input cannot offer both: `webkitdirectory` turns the dialog into a folder-only
 * picker, and there is no flag that makes one dialog accept either. A drop target can,
 * which is why dropping is the single gesture and the button is the fallback.
 *
 * A dropped folder arrives as a tree of `FileSystemEntry` objects rather than a list of
 * files, so it has to be walked.
 */

export type Picked = { file: File; path: string };

/** `readEntries` hands back at most a hundred at a time; an empty batch means done. */
async function children(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  const all: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((ok, no) => reader.readEntries(ok, no));
    if (!batch.length) return all;
    all.push(...batch);
  }
}

async function walk(entry: FileSystemEntry, prefix = ""): Promise<Picked[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((ok, no) => (entry as FileSystemFileEntry).file(ok, no));
    return [{ file, path: prefix + entry.name }];
  }
  const inside = await children((entry as FileSystemDirectoryEntry).createReader());
  const found = await Promise.all(inside.map((child) => walk(child, `${prefix}${entry.name}/`)));
  return found.flat();
}

/**
 * Every file in a drop, each with the path it should keep.
 *
 * Returns nothing for a drop that carries no files — dragging a source between folders
 * inside the app looks like a drop too, and that one is a move, not an upload.
 */
export async function filesIn(transfer: DataTransfer): Promise<Picked[]> {
  const entries = [...transfer.items]
    .map((item) => item.webkitGetAsEntry())
    .filter((entry): entry is FileSystemEntry => entry !== null);
  const found = await Promise.all(entries.map((entry) => walk(entry)));
  return found.flat();
}
