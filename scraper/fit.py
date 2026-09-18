"""How well a posting matches the target: semiconductor / chip design, analog, test+validation at a chip company.

fit levels, highest first:
  CHIP      real silicon work: RTL/DV/DFT/PD/analog/RF IC, or any hardware role at a semiconductor company
  HARDWARE  hardware/electrical work at a tech or deep-tech company (aerospace, robotics, quant FPGA)
  ADJACENT  EE co-op at a medical-device, consulting, HVAC, utility or similar shop
"""
import re

# companies whose hardware roles are silicon by default
SEMI_CO = re.compile(r"\b(nvidia|intel|amd\b|analog devices|marvell|nxp|microchip|broadcom|qualcomm|texas instruments|"
                     r"\bti\b|skyworks|silicon labs|wolfspeed|onsemi|allegro|monolithic power|mps\b|infineon|renesas|"
                     r"micron|samsung|sk hynix|kioxia|western digital|sandisk|seagate|rambus|synopsys|cadence|siemens eda|"
                     r"keysight|teradyne|advantest|kla\b|lam research|applied materials|asml|macom|qorvo|semtech|maxlinear|"
                     r"cirrus logic|lattice|astera|credo|eliyan|alphawave|tenstorrent|cerebras|groq|d-matrix|matx|etched|"
                     r"sambanova|rivos|sifive|ampere|lightmatter|ayar|celestial|hyperlight|quadric|axelera|untether|"
                     r"esperanto|achronix|efinix|flex logix|blaize|hailo|sima|expedera|recogni|atomic semi|finwave|"
                     r"psemi|navitas|vicor|empower|anello|nubis|xscape|lightelligence|arm\b|apple|google|meta|amazon|"
                     r"microsoft|ibm|tsmc|globalfoundries|wafer)\b", re.I)

# unmistakably silicon, whatever the company
CHIP_TITLE = re.compile(r"(?<![a-z])(asic|rtl|verilog|systemverilog|vlsi|physical design|\bdft\b|design verification|"
                        r"\bdv\b|silicon|analog|mixed.signal|rfic|serdes|ic design|\bsoc\b|semiconductor|photonic|"
                        r"integrated circuit|tapeout|synthesis|timing|\bsta\b|layout|microarchitecture|"
                        r"computer architecture|post.silicon|pre.silicon|wafer|\beda\b|\bate\b|emulation|"
                        r"characterization|device engineer|\bfpga\b|chip)(?![a-z])", re.I)

# deep-tech hardware: worth applying to, not silicon
DEEPTECH_CO = re.compile(r"\b(spacex|anduril|rocket lab|blue origin|astranis|impulse|hermeus|relativity|varda|vast\b|"
                         r"ursa major|muon|firefly|shield ai|skydio|zipline|boston dynamics|figure|apptronik|"
                         r"agility|waymo|zoox|nuro|kodiak|motional|aurora|torc|tesla|rivian|lucid|whoop|oura|"
                         r"psiquantum|ionq|rigetti|atom computing|quera|lightmatter|form energy|commonwealth fusion|"
                         r"draper|mitre|raytheon|rtx|lockheed|northrop|\bbae\b|l3harris|general dynamics|epirus|"
                         r"vannevar|saronic|hudson river|jump trading|citadel|\bdrw\b|\bimc\b|optiver|jane street|"
                         r"akuna|\bhpr\b|hyannis|samsara|applied intuition|physical intelligence|waabi|aeva|"
                         r"luminar|ouster|planet labs|formlabs|\b1x\b|dexterity|pickle)\b", re.I)

HW_TITLE = re.compile(r"(?<![a-z])(hardware|electrical engineer|electrical engineering|electronics|embedded|firmware|"
                      r"test engineer|test engineering|validation|circuit|circuits|power electronics|instrumentation|"
                      r"signal integrity|\brf\b|radio frequency|antenna|sensor)(?![a-z])", re.I)

# shops where an EE co-op is building services / medical device / utility work
ADJACENT_CO = re.compile(r"\b(consulting engineers|vanderweil|bowman|hallam|burohappold|vhb\b|wsp\b|stantec|aecom|"
                         r"fresenius|insulet|abbott|st\. jude|boston scientific|medtronic|johnson & johnson|"
                         r"\bj&j\b|becton|baxter|hologic|waters corp|instron|eisai|entrada|greensight|lennox|"
                         r"carrier|trane|schneider|eversource|national grid|socomec|dandelion|edgetech|"
                         r"belmont medical|delsys|brainco|linevision|berkshire grey|acceleron|micro-leads|"
                         r"butterfly|hyperfine|capybara|bevi|apeiron|nk labs|fikst|primaira|boston engineering|"
                         r"farm design|notch|honeybee|spacerake|blueiq|lego|midmark|battelle|sig sauer|"
                         r"united electronic|avangrid|accelevation|geotab|controlpoint|ack marine|reworld)\b", re.I)


def fit_of(job):
    """Return (fit, why). job needs company + title."""
    co, title = job.get("company", ""), job.get("title", "")
    semi_co, chip_t = bool(SEMI_CO.search(co)), bool(CHIP_TITLE.search(title))
    if chip_t and not ADJACENT_CO.search(co):
        return "CHIP", (CHIP_TITLE.search(title).group(1).lower() + (" @semi co" if semi_co else ""))
    if semi_co and HW_TITLE.search(title):
        return "CHIP", "hardware role at a semiconductor company"
    if ADJACENT_CO.search(co):
        return "ADJACENT", "non-semiconductor employer"
    if DEEPTECH_CO.search(co) and HW_TITLE.search(title):
        return "HARDWARE", "hardware at a deep-tech company"
    if HW_TITLE.search(title):
        return "HARDWARE", "hardware title"
    return "ADJACENT", ""


FIT_ORDER = {"CHIP": 0, "HARDWARE": 1, "MAYBE": 2, "ADJACENT": 3}
