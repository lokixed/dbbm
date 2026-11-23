import ActionMenu, { type ActionMenuItem } from "xedonium/ActionMenu";
import MoreVerticalIcon from "xedonium/icons/MoreVertical";

/** The three-dot actions menu at the end of a table row. */
export function RowMenu({ label, items }: { label: string; items: ActionMenuItem[] }) {
  return (
    <div className="flex justify-end">
      <ActionMenu
        label={label}
        variant="flat"
        placement="bottom-end"
        trigger={<MoreVerticalIcon className="h-4 w-4" />}
        items={items}
      />
    </div>
  );
}
