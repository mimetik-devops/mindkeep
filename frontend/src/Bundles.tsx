import { createBundle } from "./api";
import { Picker } from "./Picker";

/** The bundle picker: which knowledge base you are looking at, and a way to start another. */
export function Bundles({
  bundles,
  current,
  onPick,
  onCreate,
}: {
  bundles: string[];
  current: string;
  onPick: (name: string) => void;
  /** Called once the server has made it, so the list and the selection can follow. */
  onCreate: (name: string) => void;
}) {
  return (
    <Picker
      items={bundles.map((b) => ({ id: b, label: b }))}
      current={current}
      title="Bundles"
      placeholder="new-bundle"
      onPick={onPick}
      onCreate={async (name) => {
        await createBundle(name);
        onCreate(name);
        return name;
      }}
    />
  );
}
