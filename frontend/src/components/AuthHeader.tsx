import type { ReactNode } from "react";
import BodyText from "xedonium/BodyText";
import Flex from "xedonium/Flex";
import PageTitle from "xedonium/PageTitle";
import { Logo } from "@/components/Logo";

interface AuthHeaderProps {
  title: string;
  subtitle: ReactNode;
}

// Header of the sign-in and setup cards: the logo on the left, the title with its subtext beside it.
export function AuthHeader({ title, subtitle }: AuthHeaderProps) {
  return (
    <Flex align="center" gap={4} className="mb-5">
      <Logo size={48} />
      <Flex direction="column" gap={0}>
        <PageTitle as="h1" className="!text-lg sm:!text-lg">
          {title}
        </PageTitle>
        <BodyText className="!text-app-muted">{subtitle}</BodyText>
      </Flex>
    </Flex>
  );
}
