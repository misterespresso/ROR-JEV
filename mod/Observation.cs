using RoR2;
using RoR2.Projectile;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Rainflayer
{
    /// <summary>
    /// QUERY_OBSERVATION -- ROR-JEV addition, not in upstream.
    ///
    /// The first fields of protocol v2's single snapshot (docs/05-PROTOCOL.md section 4), added
    /// because stock sends none of them and the catalog cannot ask JEV anything without them
    /// (docs/08-PHASE0-REPORT.md F18): elapsed time, difficulty, survivor identity, player
    /// health, and the four hazard facts.
    ///
    /// Everything is read in ONE main-thread pass, so these fields describe the same instant --
    /// unlike stock's separate queries, which disagreed on 1 of 10,578 ticks (F22).
    ///
    /// Numbers go out as JSON numbers. Stock's events send them as strings (F11); this does not.
    /// A value that cannot be read is omitted rather than defaulted, so the brain's field
    /// contract sees it as unknown instead of as a reassuring zero.
    /// </summary>
    internal static class Observation
    {
        /// <summary>Environmental damage within this window counts as "taking" it now.</summary>
        const float ENV_DAMAGE_WINDOW_S = 2f;

        /// <summary>Hostile projectiles counted within this radius of the player.</summary>
        const float PROJECTILE_RADIUS_M = 15f;

        /// <summary>Enemies counted within this radius -- the same 50 m stock COMBAT_STATUS uses.</summary>
        const float THREAT_RADIUS_M = 50f;

        /// <summary>At most this many enemies are listed (nearest first); the counts cover all of them.</summary>
        const int THREAT_LIST_MAX = 16;

        /// <summary>At most this many interactables are listed (nearest first); `total` counts all.</summary>
        const int INTERACTABLE_LIST_MAX = 48;

        /// <summary>Refresh the interactable object lists this often; state is re-read every snapshot.</summary>
        const float INTERACTABLE_CACHE_S = 2f;

        static PurchaseInteraction[] purchaseCache = new PurchaseInteraction[0];
        static BarrelInteraction[] barrelCache = new BarrelInteraction[0];
        static float interactableCacheAt = -999f;

        /// <summary>FindObjectsOfType is too expensive per snapshot; refresh the fog list this often.</summary>
        const float FOG_CACHE_S = 5f;

        static float lastEnvironmentalDamageAt = -1f;
        static FogDamageController[] fogCache = new FogDamageController[0];
        static float fogCacheAt = -999f;

        /// <summary>
        /// Called from the player damage hook for damage with no character behind it: falls,
        /// lava, void fog, hazards. Damage from a character -- including its DoT ticks, which
        /// carry the attacker who applied them -- is not environmental.
        /// </summary>
        public static void NoteEnvironmentalDamage()
        {
            lastEnvironmentalDamageAt = Time.realtimeSinceStartup;
        }

        public static string Build(CharacterBody? body)
        {
            var run = Run.instance;
            var top = new List<string>
            {
                P("type", S("OBSERVATION")),
                P("v", "1"),
                P("t_mono_ms", F(Time.realtimeSinceStartup * 1000f)),
            };

            bool inRun = run != null && body != null && body.healthComponent != null;
            top.Add(P("in_run", B(inRun)));
            if (!inRun)
            {
                top.Add(P("note", S("Not in game")));
                return "{" + string.Join(",", top) + "}";
            }

            // --- run ---------------------------------------------------------------
            var runFields = new List<string>
            {
                P("survivor", S(BodyCatalog.GetBodyName(body!.bodyIndex) ?? "")),
                P("difficulty", S(run!.selectedDifficulty.ToString())),
                P("difficulty_coefficient", F(run.difficultyCoefficient)),
                P("elapsed_s", F(run.GetRunStopwatch())),
                P("stage_clear_count", run.stageClearCount.ToString(CultureInfo.InvariantCulture)),
                P("scene", S(SceneManager.GetActiveScene().name)),
            };

            // --- self --------------------------------------------------------------
            var hc = body.healthComponent;
            var selfFields = new List<string>
            {
                P("hp", F(hc.health)),
                P("hp_max", F(hc.fullHealth)),
                P("shield", F(hc.shield)),
                P("shield_max", F(hc.fullShield)),
                P("barrier", F(hc.barrier)),
                P("alive", B(hc.alive)),
            };
            // ROR-JEV (build 10): equipment readiness. Stock INVENTORY names the equipment but
            // not whether it can fire, so use_equipment_now could never be asked (jev-05).
            var slot = body.equipmentSlot;
            var eqDef = EquipmentCatalog.GetEquipmentDef(body.inventory ? body.inventory.currentEquipmentIndex : EquipmentIndex.None);
            selfFields.Add(P("equipment", eqDef == null ? "null" : "{" + string.Join(",", new[]
            {
                P("name", S(eqDef.name ?? "")),
                P("charges", (slot ? slot.stock : 0).ToString(CultureInfo.InvariantCulture)),
                P("ready", B(slot && slot.stock > 0)),
            }) + "}"));

            // --- hazards -----------------------------------------------------------
            float now = Time.realtimeSinceStartup;
            float envAge = lastEnvironmentalDamageAt < 0f ? -1f : now - lastEnvironmentalDamageAt;
            var hazardFields = new List<string>
            {
                P("taking_environmental_damage", B(envAge >= 0f && envAge <= ENV_DAMAGE_WINDOW_S)),
                P("environmental_damage_age_s", envAge < 0f ? "null" : F(envAge)),
                P("active_dots", Arr(ActiveDots(body))),
                P("hostile_projectiles_nearby", HostileProjectiles(body).ToString(CultureInfo.InvariantCulture)),
                P("hostile_projectiles_radius_m", F(PROJECTILE_RADIUS_M)),
                P("in_hazard_volume", B(InVoidFog(body))),
                // What `in_hazard_volume` covers, stated on the wire so no reader over-trusts it:
                // being damaged by a void-fog FogDamageController. Lava, sulfur pools and similar
                // stage hazards are NOT covered; their damage shows up as environmental damage.
                P("in_hazard_volume_covers", S("void_fog")),
            };

            top.Add(P("run", "{" + string.Join(",", runFields) + "}"));
            top.Add(P("self", "{" + string.Join(",", selfFields) + "}"));
            top.Add(P("hazards", "{" + string.Join(",", hazardFields) + "}"));
            top.Add(P("threat", Threat(body)));
            top.Add(P("interactables", Interactables(body)));
            return "{" + string.Join(",", top) + "}";
        }

        /// <summary>
        /// Raw threat facts, no judgment: which bodies, how far, elite or boss, and whether each
        /// one's AI is currently targeting the player. Classification into enemy classes, and
        /// banding "being focused", are the brain's job (brain/stock_adapter.py) so the tables
        /// behind them can be reviewed without a rebuild.
        ///
        /// Hostile = alive, and on any team other than the player's -- the same rule stock
        /// COMBAT_STATUS uses, plus the alive check it lacks. `team` is sent so the brain can
        /// see what that rule includes.
        /// </summary>
        static string Threat(CharacterBody body)
        {
            var ourTeam = body.teamComponent.teamIndex;
            var me = body.gameObject;
            var found = new List<KeyValuePair<float, CharacterBody>>();
            int elites = 0, bosses = 0, targeting = 0;

            foreach (var other in CharacterBody.readOnlyInstancesList)
            {
                if (other == null || other == body || other.teamComponent == null
                    || other.teamComponent.teamIndex == ourTeam
                    || other.healthComponent == null || !other.healthComponent.alive)
                    continue;
                float d = Vector3.Distance(body.corePosition, other.corePosition);
                if (d > THREAT_RADIUS_M)
                    continue;
                found.Add(new KeyValuePair<float, CharacterBody>(d, other));
                if (other.isElite) elites++;
                if (other.isBoss) bosses++;
                if (Targets(other, me)) targeting++;
            }
            found.Sort((x, y) => x.Key.CompareTo(y.Key));

            var list = new List<string>();
            for (int i = 0; i < found.Count && i < THREAT_LIST_MAX; i++)
            {
                var b = found[i].Value;
                list.Add("{" + string.Join(",", new[]
                {
                    P("body", S(BodyCatalog.GetBodyName(b.bodyIndex) ?? "")),
                    P("team", S(b.teamComponent.teamIndex.ToString())),
                    P("is_elite", B(b.isElite)),
                    P("is_boss", B(b.isBoss)),
                    P("is_flying", B(b.isFlying)),
                    P("distance", F(found[i].Key)),
                    P("targeting_player", B(Targets(b, me))),
                }) + "}");
            }

            return "{" + string.Join(",", new[]
            {
                P("radius_m", F(THREAT_RADIUS_M)),
                P("enemy_count", found.Count.ToString(CultureInfo.InvariantCulture)),
                P("elite_count", elites.ToString(CultureInfo.InvariantCulture)),
                P("boss_count", bosses.ToString(CultureInfo.InvariantCulture)),
                P("targeting_player_count", targeting.ToString(CultureInfo.InvariantCulture)),
                P("listed", list.Count.ToString(CultureInfo.InvariantCulture)),
                P("enemies", "[" + string.Join(",", list) + "]"),
            }) + "}";
        }

        /// <summary>
        /// Every purchasable object and every barrel on the stage (build 6, F12): no view cone, no
        /// line-of-sight test, no name filter. Stock's QUERY_INTERACTABLES keeps only names with
        /// "shrine" or "chest" inside a 180-degree cone, and never sees barrels at all -- they are
        /// BarrelInteraction, not PurchaseInteraction. Raw facts only; the brain names the types.
        ///
        /// `id` is the Unity instance ID: stable for the object's life within a stage, so the brain
        /// can track one chest across observations (F17). Only available objects are listed.
        /// </summary>
        static string Interactables(CharacterBody body)
        {
            float now = Time.realtimeSinceStartup;
            if (now - interactableCacheAt > INTERACTABLE_CACHE_S)
            {
                purchaseCache = UnityEngine.Object.FindObjectsOfType<PurchaseInteraction>();
                barrelCache = UnityEngine.Object.FindObjectsOfType<BarrelInteraction>();
                interactableCacheAt = now;
            }

            Vector3 me = body.corePosition;
            var found = new List<KeyValuePair<float, string>>();
            int unavailable = 0;

            foreach (var pi in purchaseCache)
            {
                if (pi == null) continue;
                if (!pi.available) { unavailable++; continue; }
                Vector3 pos = pi.transform.position;
                float d = Vector3.Distance(me, pos);
                var fields = new List<string>
                {
                    P("id", pi.gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture)),
                    P("kind", S("purchase")),
                    P("prefab", S(Prefab(pi.gameObject))),
                    P("display", S(pi.GetDisplayName() ?? "")),
                    P("cost", pi.cost.ToString(CultureInfo.InvariantCulture)),
                    P("cost_type", S(pi.costType.ToString())),
                    P("distance", F(d)),
                    P("height", F(pos.y - me.y)),
                };
                var shop = pi.GetComponent<ShopTerminalBehavior>();
                if (shop != null)
                {
                    bool hidden = shop.pickupIndexIsHidden;
                    var def = hidden ? null : PickupCatalog.GetPickupDef(shop.CurrentPickupIndex());
                    fields.Add(P("shop_hidden", B(hidden)));
                    fields.Add(P("shop_item", def != null ? S(def.internalName ?? "") : "null"));
                }
                found.Add(new KeyValuePair<float, string>(d, "{" + string.Join(",", fields) + "}"));
            }

            foreach (var bi in barrelCache)
            {
                if (bi == null) continue;
                if (bi.Networkopened) { unavailable++; continue; }
                Vector3 pos = bi.transform.position;
                float d = Vector3.Distance(me, pos);
                found.Add(new KeyValuePair<float, string>(d, "{" + string.Join(",", new[]
                {
                    P("id", bi.gameObject.GetInstanceID().ToString(CultureInfo.InvariantCulture)),
                    P("kind", S("barrel")),
                    P("prefab", S(Prefab(bi.gameObject))),
                    P("display", S(bi.GetDisplayName() ?? "")),
                    P("cost", "0"),
                    P("cost_type", S("None")),
                    P("distance", F(d)),
                    P("height", F(pos.y - me.y)),
                }) + "}"));
            }

            found.Sort((x, y) => x.Key.CompareTo(y.Key));
            var list = new List<string>();
            for (int i = 0; i < found.Count && i < INTERACTABLE_LIST_MAX; i++)
                list.Add(found[i].Value);

            return "{" + string.Join(",", new[]
            {
                P("total", found.Count.ToString(CultureInfo.InvariantCulture)),
                P("unavailable", unavailable.ToString(CultureInfo.InvariantCulture)),
                P("listed", list.Count.ToString(CultureInfo.InvariantCulture)),
                P("items", "[" + string.Join(",", list) + "]"),
            }) + "}";
        }

        /// <summary>
        /// The available interactable with this instance ID (build 8, INTERACT_ID), or null if
        /// it no longer exists or has been used. Same lists and availability rule as the
        /// snapshot, refreshed first if stale, so an ID JEV saw is resolved the same way.
        /// </summary>
        public static GameObject? FindInteractable(int id, out int cost, out string costType)
        {
            cost = 0;
            costType = "None";
            if (Time.realtimeSinceStartup - interactableCacheAt > INTERACTABLE_CACHE_S)
            {
                purchaseCache = UnityEngine.Object.FindObjectsOfType<PurchaseInteraction>();
                barrelCache = UnityEngine.Object.FindObjectsOfType<BarrelInteraction>();
                interactableCacheAt = Time.realtimeSinceStartup;
            }
            foreach (var pi in purchaseCache)
            {
                if (pi == null || pi.gameObject.GetInstanceID() != id) continue;
                if (!pi.available) return null;
                cost = pi.cost;
                costType = pi.costType.ToString();
                return pi.gameObject;
            }
            foreach (var bi in barrelCache)
            {
                if (bi == null || bi.gameObject.GetInstanceID() != id) continue;
                return bi.Networkopened ? null : bi.gameObject;
            }
            return null;
        }

        /// <summary>The prefab name: the GameObject name without Unity's "(Clone)" suffix.</summary>
        static string Prefab(GameObject go)
        {
            string n = go.name ?? "";
            int i = n.IndexOf("(Clone)", StringComparison.Ordinal);
            return (i >= 0 ? n.Substring(0, i) : n).Trim();
        }

        /// <summary>True if any of the body's AIs has the player as its current enemy.</summary>
        static bool Targets(CharacterBody other, GameObject player)
        {
            var master = other.master;
            if (master == null)
                return false;
            // CharacterMaster.aiComponents is not public in this build; the AIs sit on the master.
            foreach (var ai in master.GetComponents<RoR2.CharacterAI.BaseAI>())
                if (ai != null && ai.currentEnemy != null && ai.currentEnemy.gameObject == player)
                    return true;
            return false;
        }

        static List<string> ActiveDots(CharacterBody body)
        {
            var dots = new List<string>();
            var dc = DotController.FindDotController(body.gameObject);
            if (dc == null)
                return dots;
            foreach (DotController.DotIndex idx in Enum.GetValues(typeof(DotController.DotIndex)))
            {
                if ((int)idx < 0 || idx.ToString() == "Count")
                    continue;
                if (dc.HasDotActive(idx))
                    dots.Add(idx.ToString());
            }
            return dots;
        }

        static int HostileProjectiles(CharacterBody body)
        {
            int ourTeam = (int)body.teamComponent.teamIndex;
            var seen = new HashSet<ProjectileController>();
            int count = 0;
            var hits = Physics.OverlapSphere(body.corePosition, PROJECTILE_RADIUS_M,
                LayerIndex.projectile.mask, QueryTriggerInteraction.Collide);
            foreach (var c in hits)
            {
                var pc = c.GetComponentInParent<ProjectileController>();
                if (pc == null || !seen.Add(pc))
                    continue;
                var tf = pc.teamFilter;
                if (tf != null && (int)tf.teamIndex != ourTeam)
                    count++;
            }
            return count;
        }

        static bool InVoidFog(CharacterBody body)
        {
            float now = Time.realtimeSinceStartup;
            if (now - fogCacheAt > FOG_CACHE_S)
            {
                fogCache = UnityEngine.Object.FindObjectsOfType<FogDamageController>();
                fogCacheAt = now;
            }
            foreach (var fog in fogCache)
            {
                if (fog == null)
                    continue;
                foreach (var affected in fog.GetAffectedBodies())
                    if (affected == body)
                        return true;
            }
            return false;
        }

        // --- tiny JSON writer: invariant culture, real numbers, escaped strings --------------

        static string P(string key, string value) => "\"" + key + "\":" + value;

        static string B(bool b) => b ? "true" : "false";

        static string F(float v) =>
            float.IsNaN(v) || float.IsInfinity(v) ? "null" : v.ToString("0.###", CultureInfo.InvariantCulture);

        static string Arr(List<string> items)
        {
            var sb = new StringBuilder("[");
            for (int i = 0; i < items.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append(S(items[i]));
            }
            return sb.Append(']').ToString();
        }

        static string S(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char ch in s)
            {
                switch (ch)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (ch < 0x20) sb.Append("\\u").Append(((int)ch).ToString("x4"));
                        else sb.Append(ch);
                        break;
                }
            }
            return sb.Append('"').ToString();
        }
    }
}
