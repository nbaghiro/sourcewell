import { Plus, Users } from "lucide-react";
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { DataError } from "@/components/data-error";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { type ContactIn, useContacts, useImportContacts } from "@/lib/api/queries";

export function ContactsPage() {
  const { data, isLoading, isError, refetch } = useContacts();
  const navigate = useNavigate();

  return (
    <PageLayout>
      <PageHeader
        eyebrow="Sourcing"
        title="Contacts"
        description="People sourced into this workspace. Rank them into a campaign to start outreach."
      >
        <ImportDialog />
      </PageHeader>

      {isError ? (
        <DataError onRetry={() => void refetch()} />
      ) : isLoading ? (
        <Skeleton className="h-96" />
      ) : !data || data.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No contacts yet"
          description="Import your own list to start ranking candidates."
          action={<ImportDialog />}
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Skills</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((c) => (
                <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/contacts/${c.id}`)}>
                  <TableCell>
                    <PersonCell name={c.full_name} subtitle={c.email ?? undefined} imageSrc={c.avatar_url ?? undefined} />
                  </TableCell>
                  <TableCell>{c.title ?? "—"}</TableCell>
                  <TableCell>{c.company ?? "—"}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {c.skills.slice(0, 3).map((s) => (
                        <Badge key={s} variant="secondary">{s}</Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{c.source}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </PageLayout>
  );
}

const PLACEHOLDER = `Jane Doe, Senior Backend Engineer, Acme, jane@acme.com, python;go
Marcus Lee, Staff Engineer, Globex, marcus@globex.com, distributed systems;kafka`;

function ImportDialog() {
  const [text, setText] = React.useState("");
  const importContacts = useImportContacts();

  function submit() {
    const contacts: ContactIn[] = text
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((line) => {
        const [full_name, title, company, email, skills] = line.split(",").map((s) => s.trim());
        return {
          full_name,
          title: title || null,
          company: company || null,
          email: email || null,
          skills: skills ? skills.split(";").map((s) => s.trim()).filter(Boolean) : [],
          tags: [],
        };
      })
      .filter((c) => c.full_name);
    if (contacts.length === 0) return;
    importContacts.mutate(contacts, {
      onSuccess: () => {
        toast.success(`Imported ${contacts.length} contact${contacts.length === 1 ? "" : "s"}`);
        setText("");
      },
      onError: () => toast.error("Import failed"),
    });
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus /> Import
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import contacts</DialogTitle>
          <DialogDescription>
            One per line: <span className="font-mono text-xs">Name, Title, Company, Email, skill;skill</span>
          </DialogDescription>
        </DialogHeader>
        <Textarea rows={7} value={text} onChange={(e) => setText(e.target.value)} placeholder={PLACEHOLDER} className="font-mono text-xs" />
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <DialogClose asChild>
            <Button disabled={importContacts.isPending || !text.trim()} onClick={submit}>
              Import
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
