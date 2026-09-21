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
            return "{" + string.Join(",", top) + "}";
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
