import * as maplibregl from "maplibre-gl";
import type { StyleSpecification } from "maplibre-gl";
// バンドル後は既定の worker の場所が変わるため、ビルドに含めた worker の URL を明示する
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { deviceColor, deviceLabel, formatTime, timeAgo, type Device, type Place, type Track } from "./api";

// 既定の背景地図: 国土地理院の標準地図(API キー不要)。MAP_STYLE_URL を設定すればそちらを使う
const GSI_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    gsi: {
      type: "raster",
      tiles: ["https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 18,
      attribution:
        '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
    },
  },
  layers: [{ id: "gsi", type: "raster", source: "gsi" }],
};

maplibregl.setWorkerUrl(workerUrl);

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export type Focus = { lon: number; lat: number; key: number };
export type Center = { lat: number; lon: number };

/** 場所(中心と半径)を地図に描く円の多角形にする。 */
function circle(p: Place): GeoJSON.Feature {
  const coords: [number, number][] = [];
  const dLat = p.radius_m / 111320;
  const dLon = p.radius_m / (111320 * Math.cos((p.lat * Math.PI) / 180));
  for (let i = 0; i <= 64; i++) {
    const a = (i / 64) * 2 * Math.PI;
    coords.push([p.lon + dLon * Math.cos(a), p.lat + dLat * Math.sin(a)]);
  }
  return { type: "Feature", geometry: { type: "Polygon", coordinates: [coords] }, properties: { name: p.name } };
}

type Props = {
  styleUrl: string | null;
  devices: Device[];
  selectedId: number | null;
  track: Track | null;
  trackFitKey: number;
  focus: Focus | null;
  places: Place[];
  centerRef: MutableRefObject<Center | null>;
  onSelect: (id: number) => void;
};

function el(tag: string, className: string, text?: string) {
  const e = document.createElement(tag);
  e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function popupContent(d: Device) {
  const box = el("div", "popup");
  box.append(el("strong", "", deviceLabel(d)));
  if (d.last) {
    const parts = [`${formatTime(d.last.recorded_at)}(${timeAgo(d.last.recorded_at)})`];
    if (d.last.acc != null) parts.push(`精度 ±${Math.round(d.last.acc)} m`);
    if (d.last.batt != null) parts.push(`電池 ${d.last.batt}%`);
    if (d.last.vel) parts.push(`${Math.round(d.last.vel)} km/h`);
    for (const p of parts) box.append(el("div", "", p));
  }
  return box;
}

export default function MapView({ styleUrl, devices, selectedId, track, trackFitKey, focus, places, centerRef, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markers = useRef(new Map<number, maplibregl.Marker>());
  const fitted = useRef(false);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [ready, setReady] = useState(false);

  // 地図の作成と破棄
  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: styleUrl ?? GSI_STYLE,
      center: [139.767, 35.681],
      zoom: 11,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({}), "bottom-left");
    const saveCenter = () => {
      const c = map.getCenter();
      centerRef.current = { lat: c.lat, lon: c.lng };
    };
    saveCenter();
    map.on("moveend", saveCenter);
    map.on("load", () => {
      map.addSource("places", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "places-fill",
        type: "fill",
        source: "places",
        paint: { "fill-color": "#8250df", "fill-opacity": 0.15 },
      });
      map.addLayer({
        id: "places-line",
        type: "line",
        source: "places",
        paint: { "line-color": "#8250df", "line-width": 2, "line-dasharray": [2, 1] },
      });
      map.addSource("track", { type: "geojson", data: EMPTY });
      map.addLayer({
        id: "track-line",
        type: "line",
        source: "track",
        filter: ["==", ["get", "kind"], "track"],
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": "#1f6feb", "line-width": 4, "line-opacity": 0.8 },
      });
      map.addLayer({
        id: "track-points",
        type: "circle",
        source: "track",
        filter: ["==", ["get", "kind"], "point"],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.5, 16, 4],
          "circle-color": "#ffffff",
          "circle-stroke-color": "#1f6feb",
          "circle-stroke-width": 1.5,
        },
      });
      setReady(true);
    });
    map.on("click", "track-points", (e) => {
      const p = e.features?.[0]?.properties as Record<string, unknown> | undefined;
      if (!p) return;
      const box = el("div", "popup");
      box.append(el("strong", "", formatTime(String(p.recorded_at))));
      if (p.acc != null) box.append(el("div", "", `精度 ±${Math.round(Number(p.acc))} m`));
      if (p.vel != null && Number(p.vel) > 0) box.append(el("div", "", `${Math.round(Number(p.vel))} km/h`));
      if (p.batt != null) box.append(el("div", "", `電池 ${p.batt}%`));
      new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setDOMContent(box).addTo(map);
    });
    map.on("click", "places-fill", (e) => {
      if (map.queryRenderedFeatures(e.point, { layers: ["track-points"] }).length) return;
      const name = e.features?.[0]?.properties?.name;
      if (name) new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat).setDOMContent(el("strong", "", String(name))).addTo(map);
    });
    map.on("mouseenter", "track-points", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "track-points", () => (map.getCanvas().style.cursor = ""));
    mapRef.current = map;
    const ms = markers.current;
    return () => {
      ms.forEach((m) => m.remove());
      ms.clear();
      map.remove();
      mapRef.current = null;
      fitted.current = false;
      setReady(false);
    };
  }, [styleUrl]);

  // 現在地マーカー
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const seen = new Set<number>();
    for (const d of devices) {
      if (!d.last) continue;
      seen.add(d.id);
      let m = markers.current.get(d.id);
      if (!m) {
        const node = el("button", "marker") as HTMLButtonElement;
        node.type = "button";
        node.addEventListener("click", (e) => {
          e.stopPropagation();
          onSelectRef.current(d.id);
        });
        m = new maplibregl.Marker({ element: node }).setLngLat([d.last.lon, d.last.lat]).addTo(map);
        m.setPopup(new maplibregl.Popup({ offset: 18, closeButton: false }));
        markers.current.set(d.id, m);
      }
      m.setLngLat([d.last.lon, d.last.lat]);
      const node = m.getElement();
      node.textContent = d.tid || d.name.slice(0, 2);
      node.title = deviceLabel(d);
      node.style.background = deviceColor(d.id);
      node.classList.toggle("selected", d.id === selectedId);
      m.getPopup()?.setDOMContent(popupContent(d));
    }
    for (const [id, m] of markers.current) {
      if (!seen.has(id)) {
        m.remove();
        markers.current.delete(id);
      }
    }
    // 最初の 1 回だけ、全員が入るように表示範囲を合わせる
    const located = devices.filter((d) => d.last);
    if (!fitted.current && located.length) {
      fitted.current = true;
      if (located.length === 1) {
        map.jumpTo({ center: [located[0].last!.lon, located[0].last!.lat], zoom: 14 });
      } else {
        const b = new maplibregl.LngLatBounds();
        located.forEach((d) => b.extend([d.last!.lon, d.last!.lat]));
        map.fitBounds(b, { padding: 60, maxZoom: 15, duration: 0 });
      }
    }
  }, [devices, selectedId]);

  // 登録した場所
  useEffect(() => {
    const src = mapRef.current?.getSource("places") as maplibregl.GeoJSONSource | undefined;
    src?.setData({ type: "FeatureCollection", features: places.map(circle) });
  }, [places, ready]);

  // 履歴の線
  useEffect(() => {
    const src = mapRef.current?.getSource("track") as maplibregl.GeoJSONSource | undefined;
    src?.setData(track ?? EMPTY);
  }, [track, ready]);

  // 履歴を読み込んだときは、経路全体が見えるように合わせる
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !track || trackFitKey === 0) return;
    const b = new maplibregl.LngLatBounds();
    for (const f of track.features) if (f.geometry.type === "Point") b.extend(f.geometry.coordinates as [number, number]);
    if (!b.isEmpty()) map.fitBounds(b, { padding: 60, maxZoom: 16 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trackFitKey]);

  useEffect(() => {
    if (focus) mapRef.current?.flyTo({ center: [focus.lon, focus.lat], zoom: Math.max(mapRef.current.getZoom(), 14) });
  }, [focus]);

  return <div ref={container} className="map" />;
}
