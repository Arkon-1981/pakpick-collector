import { notFound } from "next/navigation";
import { DEALS } from "@/lib/data";
import { DetailView } from "../../components/DetailView";

/* 모든 게임의 상세 페이지를 빌드 시점에 미리 생성 (더미 데이터 단계) */
export function generateStaticParams() {
  return DEALS.map((d) => ({ id: d.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const deal = DEALS.find((d) => d.id === id);
  if (!deal) return {};
  return {
    title: `${deal.title} 할인 -${deal.disc}% | Pakpick`,
    description: `${deal.title} — 현재 ${deal.disc}% 할인 중. 역대 최저가와 가격 변동 그래프를 팩픽에서 확인하세요.`,
  };
}

export default async function GameDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const deal = DEALS.find((d) => d.id === id);
  if (!deal) notFound();
  return <DetailView deal={deal} />;
}
