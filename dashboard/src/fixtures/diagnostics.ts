import fixtureSnapshot4 from './snapshot.json';
import fixtureSnapshot3 from './snapshot.3.json';
import pairResponse from './pairEntitiesResponse.json';
import { FIXTURE_VARIANT } from '../config/env';
import type { BusesResponse, PairEntitiesResponse, PlatformEntitiesResponse } from '../types/diagnostics';

type FixtureSnapshot = typeof fixtureSnapshot4;

function activeFixture(): FixtureSnapshot {
  return (FIXTURE_VARIANT === '3' ? fixtureSnapshot3 : fixtureSnapshot4) as unknown as FixtureSnapshot;
}

// Copy of relay/tests/_http/fixtures/pair_entities_response.json, the relay's real response shape.
const RELAY_PAIR = pairResponse as unknown as PairEntitiesResponse;

export function buildFixturePairEntities(from: string, to: string): PairEntitiesResponse {
  return { ...RELAY_PAIR, from, to };
}

export function buildFixturePlatformEntities(platformId: string): PlatformEntitiesResponse {
  const snapshot = activeFixture();
  const otherIds = (snapshot.platforms ?? []).map((p) => p.id).filter((id) => id !== platformId);
  return {
    platform: platformId,
    outgoing: otherIds.map((to) => ({ to, entities: RELAY_PAIR.entities })),
    incoming: otherIds.map((from) => ({ from, entities: RELAY_PAIR.entities })),
  };
}

export function buildFixtureBuses(): BusesResponse {
  const snapshot = activeFixture();
  const servers = (snapshot.servers ?? snapshot.platforms ?? []) as Array<{ id: string; usedBuses?: string[] }>;
  return (snapshot.buses ?? []).map((b) => ({
    id: (b as { id?: string }).id ?? b.name,
    name: b.name,
    ok: b.ok,
    blockedBy: b.blockedBy,
    topics: b.topics,
    usedBy: servers.filter((s) => (s.usedBuses ?? []).includes((b as { id?: string }).id ?? b.name)).map((s) => s.id),
  }));
}
