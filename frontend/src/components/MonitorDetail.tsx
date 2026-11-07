/**
 * MonitorDetail (REDESIGN) — the selected entity's detail, shown as a floating
 * panel over the on-demand map (overlay-reveal). This is the same *SearchDetail
 * content that used to live in the sidebar workspace; when a result card is opened
 * the workspace hides and the map is revealed, so the detail rides ON the map here
 * (identity + facts + track controls) beside the drawn track. Entity mode only —
 * area mode reveals the map for the drawn box / crossing track with no detail card.
 */

import type { SearchEntity } from "./SearchPanel";
import type {
  AircraftSummary, AirportSummary, LandingCandidate, SatelliteSummary,
  VesselSummary, VesselTrackPoint,
} from "../api/types";
import VesselSearchDetail from "./VesselSearchDetail";
import AircraftSearchDetail from "./AircraftSearchDetail";
import SatelliteSearchDetail from "./SatelliteSearchDetail";
import AirportSearchDetail from "./AirportSearchDetail";

interface Props {
  entity: SearchEntity;
  expanded: boolean;
  onToggleTrack: () => void;
  onClear: () => void;
  vesselSummary: VesselSummary | null;
  vesselTrack: VesselTrackPoint[] | null;
  vesselTrackLoading: boolean;
  vesselTrackHasMore: boolean;
  aircraftSummary: AircraftSummary | null;
  aircraftTrail: [number, number, number | null][] | null;
  aircraftTrackLoading: boolean;
  aircraftTrackHasMore: boolean;
  satelliteSummary: SatelliteSummary | null;
  airportSummary: AirportSummary | null;
  landings: LandingCandidate[];
  landingLoading: boolean;
  landingSelected: string | null;
  onSelectLanding: (icao24: string | null) => void;
}

export default function MonitorDetail(props: Props) {
  const {
    entity, expanded, onToggleTrack, onClear,
    vesselSummary, vesselTrack, vesselTrackLoading, vesselTrackHasMore,
    aircraftSummary, aircraftTrail, aircraftTrackLoading, aircraftTrackHasMore,
    satelliteSummary,
    airportSummary, landings, landingLoading, landingSelected, onSelectLanding,
  } = props;

  return (
    <div className="map-detail" role="region">
      {entity === "vessel" ? (
        <VesselSearchDetail
          summary={vesselSummary}
          track={vesselTrack}
          trackLoading={vesselTrackLoading}
          trackHasMore={vesselTrackHasMore}
          expanded={expanded}
          onToggleTrack={onToggleTrack}
          onClear={onClear}
        />
      ) : entity === "aircraft" ? (
        <AircraftSearchDetail
          summary={aircraftSummary}
          trail={aircraftTrail}
          trackLoading={aircraftTrackLoading}
          trackHasMore={aircraftTrackHasMore}
          expanded={expanded}
          onToggleTrack={onToggleTrack}
          onClear={onClear}
        />
      ) : entity === "satellite" ? (
        <SatelliteSearchDetail
          summary={satelliteSummary}
          expanded={expanded}
          onToggleTrack={onToggleTrack}
          onClear={onClear}
        />
      ) : (
        <AirportSearchDetail
          airport={airportSummary}
          candidates={landings}
          loading={landingLoading}
          windowHours={24}
          caveat=""
          landingSelected={landingSelected}
          onSelectLanding={onSelectLanding}
        />
      )}
    </div>
  );
}
