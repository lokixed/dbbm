import { createContext, useContext, useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Modal from "xedonium/Modal";

interface FooterSlot {
  element: HTMLElement | null;
  setWanted: (wanted: boolean) => void;
}

const FooterContext = createContext<FooterSlot | null>(null);

/**
 * A Modal whose buttons live in its fixed footer instead of at the end of the scrolling body.
 * The body renders its buttons with <DialogFooter>, so they can still use the body's state.
 */
export function DialogFrame({ children, ...modalProps }: Omit<ComponentProps<typeof Modal>, "footer">) {
  const [element, setElement] = useState<HTMLElement | null>(null);
  const [wanted, setWanted] = useState(false);
  return (
    <Modal {...modalProps} footer={wanted ? <div ref={setElement} className="flex flex-wrap gap-2" /> : undefined}>
      <FooterContext.Provider value={{ element: wanted ? element : null, setWanted }}>
        {children}
      </FooterContext.Provider>
    </Modal>
  );
}

/** Puts its children into the footer of the surrounding DialogFrame. */
export function DialogFooter({ children }: { children: ReactNode }) {
  const slot = useContext(FooterContext);
  const setWanted = slot?.setWanted;
  useEffect(() => {
    setWanted?.(true);
    return () => setWanted?.(false);
  }, [setWanted]);
  return slot?.element ? createPortal(children, slot.element) : null;
}
