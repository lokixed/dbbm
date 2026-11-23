import { useState } from "react";
import Button from "xedonium/Button";
import Checkbox from "xedonium/Checkbox";
import HelperText from "xedonium/HelperText";
import Input from "xedonium/Input";
import RadioGroup from "xedonium/RadioGroup";

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
    <div className="space-y-2">
      <RadioGroup
        label="Databases to back up"
        name="db-scope"
        direction="row"
        value={all ? "all" : "selected"}
        onChange={(value) => onChange(value === "all", selected)}
        options={[
          { value: "all", label: "All databases" },
          { value: "selected", label: "Selected databases" },
        ]}
      />
      {!all && (
        <div className="space-y-2 border border-app-border p-3">
          {discovered === null && (
            <HelperText>
              Use "Save and test" to discover the databases this user can see, or add names by hand.
            </HelperText>
          )}
          {options.length > 0 && (
            <ul className="grid max-h-40 grid-cols-2 gap-1 overflow-y-auto">
              {options.map((name) => (
                <li key={name}>
                  <Checkbox label={name} checked={selected.includes(name)} onChange={() => toggle(name)} />
                </li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <Input
              value={draft}
              onChange={setDraft}
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
        <HelperText role="alert" className="!text-red-600 dark:!text-red-400">
          {error}
        </HelperText>
      )}
    </div>
  );
}
