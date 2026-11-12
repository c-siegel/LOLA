import { NotFound } from "@/components/NotFound";
import { PublicLayout } from "@/layouts/PublicLayout";

/** Screen — any address outside the dashboard that no route matches. */
export function NotFoundPage() {
  return (
    <PublicLayout>
      <NotFound />
    </PublicLayout>
  );
}
