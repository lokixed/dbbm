import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface DatabaseMultiSelectProps {
  all: boolean;
  selected: string[];
  /** Names found by the last connection test; null until a test has run. */
  discovered: string[] | null;
  onChange: (all: boolean, selected: string[]) => void;
  error?: string;
}

export function DatabaseMultiSelect({
  all,
  selected,
  discovered,
  onChange,
  error,
}: DatabaseMultiSelectProps) {
  const [draft, setDraft] = useState("");
  const options = [...new Set([...(discovered ?? []), ...selected])];

  const toggle = (name: string) =>
    onChange(false, selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name]);

  const addDraft = () => {
    const name = draft.trim();
    if (!name) return;
    if (!selected.includes(name)) onChange(false, [...selected, name]);
    setDraft("");
  };

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Databases to back up</legend>
      <div className="flex gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="radio" name="db-scope" checked={all} onChange={() => onChange(true, selected)} />
          All databases
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name="db-scope" checked={!all} onChange={() => onChange(false, selected)} />
          Selected databases
        </label>
      </div>
      {!all && (
        <div className="space-y-2 rounded-md border p-3">
          {discovered === null && (
            <p className="text-xs text-muted-foreground">
              Use "Save and test" to discover the databases this user can see, or add names by hand.
            </p>
          )}
          {options.length > 0 && (
            <ul className="grid max-h-40 grid-cols-2 gap-1 overflow-y-auto">
              {options.map((name) => (
                <li key={name}>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={selected.includes(name)} onChange={() => toggle(name)} />
                    <span className="truncate">{name}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <Input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  addDraft();
                }
              }}
              placeholder="Add a database by name"
              aria-label="Add a database by name"
            />
            <Button type="button" variant="secondary" onClick={addDraft}>
              Add
            </Button>
          </div>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </fieldset>
  );
}
