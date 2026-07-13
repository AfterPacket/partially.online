'use strict';

// Complete ISO 3166-1 numeric -> alpha-2 mapping for world-atlas topojson IDs
const NUM_TO_A2 = {
  4:'AF',8:'AL',10:'AQ',12:'DZ',20:'AD',24:'AO',28:'AG',31:'AZ',32:'AR',36:'AU',
  40:'AT',44:'BS',48:'BH',50:'BD',51:'AM',56:'BE',64:'BT',68:'BO',70:'BA',
  72:'BW',76:'BR',84:'BZ',90:'SB',96:'BN',100:'BG',104:'MM',108:'BI',
  112:'BY',116:'KH',120:'CM',124:'CA',132:'CV',140:'CF',144:'LK',148:'TD',
  152:'CL',156:'CN',158:'TW',170:'CO',174:'KM',178:'CG',180:'CD',188:'CR',
  191:'HR',192:'CU',196:'CY',203:'CZ',204:'BJ',208:'DK',212:'DM',214:'DO',
  218:'EC',222:'SV',226:'GQ',231:'ET',232:'ER',233:'EE',238:'FK',242:'FJ',
  246:'FI',250:'FR',260:'TF',262:'DJ',266:'GA',268:'GE',270:'GM',
  275:'PS',276:'DE',288:'GH',300:'GR',304:'GL',308:'GD',320:'GT',324:'GN',
  328:'GY',332:'HT',340:'HN',348:'HU',352:'IS',356:'IN',360:'ID',364:'IR',368:'IQ',
  372:'IE',376:'IL',380:'IT',384:'CI',388:'JM',392:'JP',398:'KZ',400:'JO',404:'KE',
  408:'KP',410:'KR',414:'KW',417:'KG',418:'LA',422:'LB',426:'LS',428:'LV',
  430:'LR',434:'LY',438:'LI',440:'LT',442:'LU',450:'MG',454:'MW',458:'MY',
  462:'MV',466:'ML',470:'MT',478:'MR',484:'MX',492:'MC',496:'MN',498:'MD',
  499:'ME',504:'MA',508:'MZ',512:'OM',516:'NA',524:'NP',528:'NL',540:'NC',
  548:'VU',554:'NZ',558:'NI',562:'NE',566:'NG',578:'NO',
  583:'FM',586:'PK',591:'PA',598:'PG',600:'PY',604:'PE',608:'PH',616:'PL',
  620:'PT',624:'GW',626:'TL',630:'PR',634:'QA',642:'RO',643:'RU',646:'RW',
  682:'SA',686:'SN',688:'RS',694:'SL',702:'SG',703:'SK',704:'VN',705:'SI',
  706:'SO',710:'ZA',716:'ZW',724:'ES',728:'SS',729:'SD',732:'EH',740:'SR',
  748:'SZ',752:'SE',756:'CH',760:'SY',762:'TJ',
  764:'TH',768:'TG',776:'TO',780:'TT',784:'AE',788:'TN',792:'TR',795:'TM',
  800:'UG',804:'UA',807:'MK',818:'EG',826:'GB',834:'TZ',840:'US',858:'UY',860:'UZ',
  854:'BF',862:'VE',887:'YE',894:'ZM',
};

// Numeric -> display name (so tooltips show real names, not just codes)
const NUM_TO_NAME = {
  4:'Afghanistan',8:'Albania',10:'Antarctica',12:'Algeria',20:'Andorra',24:'Angola',
  28:'Antigua \u0026 Barbuda',31:'Azerbaijan',32:'Argentina',36:'Australia',
  40:'Austria',44:'Bahamas',48:'Bahrain',50:'Bangladesh',51:'Armenia',
  56:'Belgium',64:'Bhutan',68:'Bolivia',70:'Bosnia \u0026 Herzegovina',
  72:'Botswana',76:'Brazil',84:'Belize',854:'Burkina Faso',90:'Solomon Islands',96:'Brunei',
  100:'Bulgaria',104:'Myanmar',108:'Burundi',112:'Belarus',116:'Cambodia',120:'Cameroon',
  124:'Canada',132:'Cape Verde',140:'Central African Republic',144:'Sri Lanka',
  148:'Chad',152:'Chile',156:'China',158:'Taiwan',
  170:'Colombia',174:'Comoros',178:'Rep. of Congo',
  180:'DR Congo',188:'Costa Rica',191:'Croatia',192:'Cuba',196:'Cyprus',
  203:'Czech Republic',204:'Benin',208:'Denmark',212:'Dominica',
  214:'Dominican Republic',218:'Ecuador',222:'El Salvador',
  226:'Equatorial Guinea',231:'Ethiopia',232:'Eritrea',233:'Estonia',
  238:'Falkland Islands',242:'Fiji',246:'Finland',250:'France',
  260:'French Southern Territories',262:'Djibouti',266:'Gabon',268:'Georgia',270:'Gambia',
  275:'Palestine',276:'Germany',288:'Ghana',300:'Greece',304:'Greenland',
  308:'Grenada',320:'Guatemala',324:'Guinea',328:'Guyana',332:'Haiti',
  340:'Honduras',348:'Hungary',352:'Iceland',356:'India',360:'Indonesia',364:'Iran',
  368:'Iraq',372:'Ireland',376:'Israel',380:'Italy',384:'Ivory Coast',388:'Jamaica',
  392:'Japan',398:'Kazakhstan',400:'Jordan',404:'Kenya',408:'North Korea',
  410:'South Korea',414:'Kuwait',417:'Kyrgyzstan',418:'Laos',422:'Lebanon',
  426:'Lesotho',428:'Latvia',430:'Liberia',434:'Libya',438:'Liechtenstein',
  440:'Lithuania',442:'Luxembourg',450:'Madagascar',454:'Malawi',
  458:'Malaysia',462:'Maldives',466:'Mali',470:'Malta',478:'Mauritania',
  484:'Mexico',492:'Monaco',496:'Mongolia',498:'Moldova',499:'Montenegro',
  504:'Morocco',508:'Mozambique',512:'Oman',516:'Namibia',
  524:'Nepal',528:'Netherlands',540:'New Caledonia',548:'Vanuatu',554:'New Zealand',
  558:'Nicaragua',562:'Niger',566:'Nigeria',578:'Norway',
  583:'Micronesia',586:'Pakistan',591:'Panama',598:'Papua New Guinea',600:'Paraguay',
  604:'Peru',608:'Philippines',616:'Poland',620:'Portugal',
  624:'Guinea-Bissau',626:'Timor-Leste',630:'Puerto Rico',634:'Qatar',642:'Romania',
  643:'Russia',646:'Rwanda',682:'Saudi Arabia',686:'Senegal',688:'Serbia',
  694:'Sierra Leone',702:'Singapore',703:'Slovakia',704:'Vietnam',705:'Slovenia',
  706:'Somalia',710:'South Africa',716:'Zimbabwe',724:'Spain',728:'South Sudan',729:'Sudan',
  732:'Western Sahara',740:'Suriname',748:'Eswatini',752:'Sweden',756:'Switzerland',760:'Syria',
  762:'Tajikistan',764:'Thailand',768:'Togo',776:'Tonga',780:'Trinidad \u0026 Tobago',
  784:'UAE',788:'Tunisia',792:'Turkey',795:'Turkmenistan',800:'Uganda',
  804:'Ukraine',807:'North Macedonia',818:'Egypt',826:'United Kingdom',
  834:'Tanzania',840:'United States',858:'Uruguay',860:'Uzbekistan',
  862:'Venezuela',887:'Yemen',894:'Zambia',
};

const SEV_COLORS = {
  severe:      'rgba(255, 48,  48,  0.82)',
  significant: 'rgba(255,140,   0, 0.75)',
  minor:       'rgba(255,215,   0, 0.65)',
  normal:      'rgba( 30, 90,  45, 0.40)',
  nodata:      'rgba( 18, 36,  58, 0.55)',
};

const SEV_HOVER = {
  severe:      'rgba(255, 80,  80,  0.96)',
  significant: 'rgba(255,170,  40, 0.94)',
  minor:       'rgba(255,235,  60, 0.90)',
  normal:      'rgba( 50,130,  70, 0.65)',
  nodata:      'rgba( 35, 60,  95, 0.75)',
};



// Country centroids for placing event markers on small/invisible nations
const CC_LAT_LNG = {
  'AF':[33.9,67.7],'AL':[41.2,20.2],'DZ':[28.0,2.6],'AO':[-11.2,17.9],
  'AR':[-38.4,-63.6],'AM':[40.1,45.0],'AU':[-25.3,133.8],'AT':[47.5,14.6],
  'AZ':[40.1,47.6],'BH':[26.0,50.6],'BD':[23.7,90.4],'BY':[53.7,27.9],
  'BE':[50.5,4.5],'BT':[27.5,90.4],'BO':[-16.3,-63.6],'BA':[44.2,17.9],
  'BW':[-22.3,24.7],'BF':[12.2,-1.6],'BR':[-14.2,-51.9],'BG':[42.7,25.5],'MM':[17.1,96.0],
  'BI':[-3.4,29.9],'KH':[12.6,104.9],'CM':[3.9,11.5],'CA':[56.1,-106.3],
  'CF':[6.6,20.9],'TD':[15.5,18.7],'CL':[-35.7,-71.5],'CN':[35.9,104.2],
  'CO':[4.1,-72.9],'KM':[-11.8,43.3],'CG':[-0.2,15.8],'CD':[-4.0,21.8],
  'CR':[9.7,-83.8],'HR':[45.1,15.2],'CU':[21.5,-77.8],'CY':[35.1,33.4],
  'CZ':[49.8,15.5],'DK':[56.3,9.5],'DO':[18.7,-70.2],'EC':[-1.8,-78.2],
  'EG':[26.8,30.8],'SV':[13.8,-88.9],'ER':[15.2,39.8],'EE':[58.6,25.0],
  'ET':[9.1,40.5],'FI':[61.9,25.7],'FR':[46.2,2.2],'GA':[-0.8,11.6],
  'GM':[13.4,-16.6],'GE':[41.7,44.0],'DE':[51.2,10.5],'GH':[7.9,-1.0],
  'GR':[39.1,22.0],'GT':[15.8,-90.2],'GN':[11.0,-10.9],'GY':[4.9,-58.9],
  'HT':[18.9,-72.3],'HN':[15.2,-86.2],'HU':[47.2,19.5],'IN':[20.6,78.9],
  'ID':[-0.8,113.9],'IR':[32.4,53.7],'IQ':[33.2,43.7],'IE':[53.4,-8.2],
  'IL':[31.0,34.9],'IT':[41.9,12.6],'JM':[18.1,-77.3],'JP':[36.2,138.3],
  'JO':[30.6,36.2],'KZ':[48.0,66.9],'KE':[0.0,37.9],'KP':[40.3,127.5],
  'KR':[35.9,127.8],'KW':[29.3,47.5],'KG':[41.2,74.8],'LA':[17.7,102.5],
  'LV':[56.9,24.6],'LB':[33.9,35.5],'LS':[-29.6,28.2],'LR':[6.4,-9.4],
  'LY':[26.3,17.2],'LT':[55.2,23.9],'LU':[49.8,6.1],'MG':[-18.8,46.9],
  'MW':[-13.3,34.3],'MY':[4.2,108.0],'MV':[3.2,73.2],'ML':[17.6,-2.0],
  'MR':[21.0,-10.9],'MX':[23.6,-102.6],'MD':[47.4,28.4],'MN':[46.9,103.8],
  'ME':[42.7,19.4],'MA':[31.8,-7.1],'MZ':[-18.7,35.5],'NA':[-22.9,18.5],
  'NP':[28.4,84.1],'NL':[52.1,5.3],'NI':[12.9,-85.2],'NE':[17.6,8.1],
  'NG':[9.1,8.7],'MK':[41.6,21.7],'NO':[60.5,8.5],'OM':[21.5,55.9],
  'PK':[30.4,69.3],'PA':[8.5,-80.8],'PG':[-6.3,143.9],'PY':[-23.2,-58.4],
  'PE':[-9.2,-75.0],'PH':[12.9,121.8],'PL':[51.9,19.1],'PT':[39.4,-8.2],
  'QA':[25.4,51.2],'RO':[45.9,24.9],'RU':[61.5,105.3],'RW':[-1.9,29.9],
  'SA':[23.9,45.1],'SN':[14.5,-14.5],'RS':[44.0,21.0],'SL':[8.5,-11.8],
  'SG':[1.4,103.8],'SK':[48.7,19.7],'SI':[46.2,14.8],'SO':[5.2,46.2],
  'ZA':[-28.5,24.7],'SS':[4.9,31.3],'ES':[40.5,-3.7],'LK':[7.9,80.8],
  'SD':[12.9,30.2],'SE':[60.1,18.6],'CH':[46.8,8.2],'SY':[34.8,38.9],
  'TJ':[38.9,71.3],'TZ':[-6.4,34.9],'TH':[15.9,100.9],'TG':[8.6,0.8],
  'TT':[10.7,-61.2],'TN':[33.9,9.6],'TR':[38.9,35.2],'TM':[38.9,59.6],
  'UG':[1.4,32.3],'UA':[48.4,31.2],'AE':[23.4,53.8],'GB':[55.4,-3.4],
  'US':[37.1,-95.7],'UY':[-32.5,-55.8],'UZ':[41.4,64.6],'VE':[6.4,-66.6],
  'VN':[14.1,108.3],'YE':[15.6,48.5],'ZM':[-13.1,27.8],'ZW':[-19.0,29.2],
  'PS':[31.9,35.2],'GQ':[1.7,10.3],'IS':[65.0,-18.5],'TW':[23.7,121.0],'DJ':[11.8,42.6],'CI':[7.5,-5.5],'GW':[12.0,-15.0],'NZ':[-40.9,174.9],'PR':[18.2,-66.5],
};

const _SEV_MARKER_COLOR = {
  severe:      '#ff3030',
  significant: '#ff8c00',
  minor:       '#ffd700',
};

let _eventMarkers = [];   // track markers so we can clear them on update

function _clearEventMarkers() {
  _eventMarkers.forEach(m => leafletMap.removeLayer(m));
  _eventMarkers = [];
}

function _addEventMarkers(statusList) {
  _clearEventMarkers();
  for (const c of statusList) {
    const ll = CC_LAT_LNG[c.code];
    if (!ll) continue;
    const color   = _SEV_MARKER_COLOR[c.status] || '#aaa';
    const radius  = c.status === 'severe' ? 8 : c.status === 'significant' ? 6 : 5;
    const marker  = L.circleMarker(ll, {
      radius:      radius,
      fillColor:   color,
      color:       '#fff',
      weight:      1.5,
      fillOpacity: 0.9,
      className:   'event-pulse-marker',
    });
    marker.bindTooltip(
      '<div class="map-tooltip"><div class="tt-name">' + esc(c.name) +
      ' (' + esc(c.code) + ')</div><div class="tt-sev sev-' + safeClass(c.status) + '">' +
      esc(c.status.charAt(0).toUpperCase() + c.status.slice(1)) +
      ' &mdash; ' + esc(String(c.active_events)) + ' event(s)</div></div>',
      { sticky: true, className: '', opacity: 1 }
    );
    marker.on('click', () => window.showCountryDetail(c.code));
    marker.addTo(leafletMap);
    _eventMarkers.push(marker);
  }
}

let leafletMap   = null;
let geoLayer     = null;
let countryStatus = {};

// ── Antimeridian fix ──────────────────────────────────────────────────────────
// Makes polygon ring coordinates continuous so Leaflet doesn't draw lines
// across the globe for countries that straddle the 180-degree meridian.
//
// `ref` is a longitude reference shared across every ring of ONE feature
// (via the closure created per-feature below). Without it, each ring only
// unwraps relative to its own first point — for a multi-ring country like
// Russia, that lets separate pieces (mainland vs. the small Chukotka ring
// near the Bering Strait) drift into opposite unwrap directions (+190 vs
// -180) even though they're geographically adjacent. That blows the
// feature's bounding box out to ~370 degrees wide — nearly the whole map —
// which is what Leaflet's interaction/focus outline was drawing as a giant
// box. Threading one reference through all of a feature's rings keeps every
// piece unwrapped into the same coordinate space.
function _makeRingFixer() {
  let ref = null;
  return function _fixRing(ring) {
    if (!ring.length) return ring;
    let startLng = ring[0][0];
    if (ref !== null) {
      while (startLng - ref >  180) startLng -= 360;
      while (ref - startLng >  180) startLng += 360;
    }
    const out = [[startLng, ring[0][1]]];
    for (let i = 1; i < ring.length; i++) {
      let lng = ring[i][0];
      const prev = out[i - 1][0];
      while (lng - prev >  180) lng -= 360;
      while (prev - lng >  180) lng += 360;
      out.push([lng, ring[i][1]]);
    }
    ref = out[out.length - 1][0];
    return out;
  };
}

function _fixAntimeridian(geojson) {
  for (const f of geojson.features) {
    const g = f.geometry;
    if (!g) continue;
    const fixRing = _makeRingFixer();   // fresh shared reference per feature
    if (g.type === 'Polygon') {
      g.coordinates = g.coordinates.map(fixRing);
    } else if (g.type === 'MultiPolygon') {
      g.coordinates = g.coordinates.map(poly => poly.map(fixRing));
    }
  }
  return geojson;
}

// ── Map init ──────────────────────────────────────────────────────────────────
function initMap() {
  leafletMap = L.map('map', {
    center: [20, 10], zoom: 2, minZoom: 1, maxZoom: 8,
    zoomControl: true, attributionControl: true,
  });

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: 'abcd', maxZoom: 19,
  }).addTo(leafletMap);

  _loadCountriesGeo();
}

async function _loadCountriesGeo() {
  try {
    const resp = await fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json');
    const topo = await resp.json();
    const geo  = topojson.feature(topo, topo.objects.countries);
    _fixAntimeridian(geo);   // fix Russia / Alaska / Fiji crossing the 180-degree line
    geoLayer = L.geoJSON(geo, {
      style:         _styleFeature,
      onEachFeature: _onEachFeature,
    }).addTo(leafletMap);
  } catch (err) {
    console.error('Failed to load world map:', err);
  }
}

function _name(numId) {
  return NUM_TO_NAME[numId] || null;
}

function _styleFeature(feature) {
  const id   = parseInt(feature.id);
  const a2   = NUM_TO_A2[id] || null;
  const info = a2 ? countryStatus[a2] : null;
  const sev  = info ? info.status : 'nodata';
  return {
    fillColor:   SEV_COLORS[sev] || SEV_COLORS.nodata,
    fillOpacity: 1,
    color:       'rgba(20,50,80,0.55)',
    weight:      0.5,
  };
}

function _onEachFeature(feature, layer) {
  const id   = feature.id != null ? parseInt(feature.id, 10) : NaN;
  const a2   = NUM_TO_A2[id] || null;
  const info = a2 ? countryStatus[a2] : null;
  const sev  = info ? info.status : 'nodata';
  const name = NUM_TO_NAME[id] || a2 || feature.properties.name || 'Unknown';

  layer.bindTooltip(_tooltip(name, sev, info, feature), {
    sticky: true, className: '', opacity: 1,
  });

  layer.on({
    mouseover(e) {
      const fillHov = SEV_HOVER[info ? info.status : 'nodata'] || SEV_HOVER.nodata;
      e.target.setStyle({ fillColor: fillHov, weight: 1.2, color: 'rgba(80,160,240,0.65)' });
      e.target.bringToFront();
    },
    mouseout(e)  { geoLayer.resetStyle(e.target); },
    click()      { if (a2) window.showCountryDetail(a2); },
  });
}

// ── Focus a country on the map (called when an event card / marker / shape
// is clicked, so the map actually shows where the country is) ────────────────
function focusCountry(code) {
  if (!leafletMap || !code) return;

  let matched = null;
  if (geoLayer) {
    geoLayer.eachLayer(layer => {
      if (NUM_TO_A2[parseInt(layer.feature.id)] === code) matched = layer;
    });
  }

  if (matched) {
    leafletMap.flyToBounds(matched.getBounds(), { padding: [60, 60], maxZoom: 5, duration: 0.8 });
    const fillHov = SEV_HOVER[countryStatus[code] ? countryStatus[code].status : 'nodata'] || SEV_HOVER.nodata;
    matched.setStyle({ fillColor: fillHov, weight: 2.5, color: '#fff' });
    matched.bringToFront();
    setTimeout(() => { if (geoLayer) geoLayer.resetStyle(matched); }, 1600);
  } else {
    const ll = CC_LAT_LNG[code];
    if (ll) leafletMap.flyTo(ll, Math.max(leafletMap.getZoom(), 4), { duration: 0.8 });
  }
}
window.focusCountry = focusCountry;

function _tooltip(name, sev, info, feature) {
  if (name === 'Unknown') {
    console.error('Tooltip received Unknown name:', { name, sev, info, featureId: feature ? feature.id : null, featureProperties: feature ? feature.properties : null });
  }
  const sevLabel = sev === 'nodata' ? 'No data'
    : esc(sev.charAt(0).toUpperCase() + sev.slice(1))
      + (info ? ' — ' + esc(String(info.active_events)) + ' event(s)' : '');
  return '<div class="map-tooltip">'
    + '<div class="tt-name">' + esc(name) + '</div>'
    + '<div class="tt-sev sev-' + safeClass(sev === 'nodata' ? 'normal' : sev) + '">' + sevLabel + '</div>'
    + '</div>';
}

// ── Called by app.js after each data load ─────────────────────────────────────
function updateMapColors(statusList) {
  countryStatus = {};
  for (const c of statusList) countryStatus[c.code] = c;
  _addEventMarkers(statusList);  // place dot markers (critical for small islands)

  if (!geoLayer) return;
  geoLayer.eachLayer(layer => {
    const id   = parseInt(layer.feature.id);
    const a2   = NUM_TO_A2[id] || null;
    const info = a2 ? countryStatus[a2] : null;
    const sev  = info ? info.status : 'nodata';
    layer.setStyle({
      fillColor:   SEV_COLORS[sev] || SEV_COLORS.nodata,
      fillOpacity: 1,
      color:       'rgba(20,50,80,0.55)',
      weight:      0.5,
    });
    const name = NUM_TO_NAME[id] || a2 || 'Unknown';
    layer.setTooltipContent(_tooltip(name, sev, info));
  });
}
