import Input from "xedonium/Input";
import SearchIcon from "xedonium/icons/Search";

interface SearchBoxProps {
  /** Accessible name, e.g. "Search runs". */
  label: string;
  /** Says what the search matches, e.g. "Search job, trigger or status". */
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}

/** The search field above a list page's table: same width, icon and clear button on every page. */
export function SearchBox({ label, placeholder, value, onChange }: SearchBoxProps) {
  return (
    <div className="w-full sm:w-80">
      <Input
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={onChange}
        clearable
        startAdornment={<SearchIcon className="h-4 w-4" />}
      />
    </div>
  );
}
