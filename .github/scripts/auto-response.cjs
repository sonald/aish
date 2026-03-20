const fs = require("fs");
const path = require("path");

function loadConfig() {
  const configPath = path.join(process.cwd(), ".github", "auto-response-config.json");
  return JSON.parse(fs.readFileSync(configPath, "utf8"));
}

function uniqueLabelNames(labels) {
  return new Set(
    (labels ?? [])
      .map((label) => (typeof label === "string" ? label : label?.name))
      .filter((name) => typeof name === "string" && name.length > 0),
  );
}

function extractIssueFormValue(body, field) {
  if (!body) {
    return "";
  }

  const fields = Array.isArray(field) ? field : [field];
  for (const currentField of fields) {
    const escapedField = currentField.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const regex = new RegExp(
      `(?:^|\\n)###\\s+${escapedField}\\s*\\n([\\s\\S]*?)(?=\\n###\\s+|$)`,
      "i",
    );
    const match = body.match(regex);
    if (!match) {
      continue;
    }
    for (const line of match[1].split("\n")) {
      const trimmed = line.trim();
      if (trimmed) {
        return trimmed;
      }
    }
  }

  return "";
}

function extractMarkdownSection(body, heading) {
  if (!body) {
    return "";
  }

  const escapedHeading = heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const regex = new RegExp(
    `(?:^|\\n)##\\s+${escapedHeading}\\s*\\n([\\s\\S]*?)(?=\\n##\\s+|$)`,
    "i",
  );
  const match = body.match(regex);
  return match ? match[1].trim() : "";
}

function hasMeaningfulContent(value) {
  if (!value) {
    return false;
  }

  const normalized = value
    .replace(/<!--([\s\S]*?)-->/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  if (normalized.length === 0) {
    return false;
  }

  const substantiveLines = normalized.filter((line) => {
    if (["-", "none", "n/a", "na", "无"].includes(line.toLowerCase())) {
      return false;
    }
    if (/^- \[ \]/.test(line)) {
      return false;
    }
    if (/^backward compatible\? \(yes\/no\)$/i.test(line)) {
      return false;
    }
    if (/^config changes\? \(yes\/no/i.test(line)) {
      return false;
    }
    if (/^向后兼容\? \(是\/否\)$/i.test(line)) {
      return false;
    }
    if (/^配置变更\? \(是\/否/i.test(line)) {
      return false;
    }
    return true;
  });

  return substantiveLines.length > 0;
}

function hasCheckedCheckbox(sectionBody) {
  return /- \[[xX]\]/.test(sectionBody ?? "");
}

function formatMissingItems(items) {
  return items.map((item) => `- ${item}`).join("\n");
}

function renderTemplate(template, replacements) {
  return Object.entries(replacements).reduce(
    (result, [key, value]) => result.replaceAll(`{{${key}}}`, value),
    template,
  );
}

function normalizeText(value) {
  return (value ?? "").toLowerCase();
}

async function run({ github, context }) {
  const config = loadConfig();
  const mentionRegex = /@([A-Za-z0-9-]+)/g;
  const bugSubtypeLabelSpecs = {
    regression: {
      color: "D93F0B",
      description: "Behavior that previously worked and now fails",
    },
    "bug:crash": {
      color: "B60205",
      description: "Process/app exits unexpectedly or hangs",
    },
    "bug:behavior": {
      color: "D73A4A",
      description: "Incorrect behavior without a crash",
    },
    "bug:security": {
      color: "d93f0b",
      description: "Security vulnerability",
    },
  };
  const bugTypeToLabel = {
    "Regression (worked before, now fails)": "regression",
    "Crash (process/app exits or hangs)": "bug:crash",
    "Behavior bug (incorrect output/state without crash)": "bug:behavior",
    "Security issue": "bug:security",
    "回归问题（以前正常，现在失败）": "regression",
    "崩溃问题（进程退出或卡死）": "bug:crash",
    "行为错误（结果或状态错误，但未崩溃）": "bug:behavior",
    "安全问题": "bug:security",
  };
  const bugSubtypeLabels = Object.keys(bugSubtypeLabelSpecs);
  const target = context.payload.issue ?? context.payload.pull_request;

  if (!target) {
    return;
  }

  const issue = context.payload.issue;
  const pullRequest = context.payload.pull_request;
  const comment = context.payload.comment;
  const labelSet = uniqueLabelNames(target.labels);
  const repoContext = {
    owner: context.repo.owner,
    repo: context.repo.repo,
  };
  const commentsCache = new Map();

  async function ensureLabelExists(name, color, description) {
    try {
      const existing = await github.rest.issues.getLabel({
        ...repoContext,
        name,
      });

      if (
        (color && existing.data.color?.toLowerCase() !== color.toLowerCase()) ||
        (description && (existing.data.description ?? "") !== description)
      ) {
        await github.rest.issues.updateLabel({
          ...repoContext,
          name,
          new_name: name,
          color: color ?? existing.data.color,
          description: description ?? existing.data.description,
        });
      }
    } catch (error) {
      if (error?.status !== 404) {
        throw error;
      }
      await github.rest.issues.createLabel({
        ...repoContext,
        name,
        color,
        description,
      });
    }
  }

  async function listComments(issueNumber) {
    if (!commentsCache.has(issueNumber)) {
      const comments = await github.paginate(github.rest.issues.listComments, {
        ...repoContext,
        issue_number: issueNumber,
        per_page: 100,
      });
      commentsCache.set(issueNumber, comments);
    }
    return commentsCache.get(issueNumber);
  }

  function markerTag(marker) {
    return `<!-- aish:auto-response:${marker} -->`;
  }

  async function hasMarker(issueNumber, marker) {
    const comments = await listComments(issueNumber);
    return comments.some((item) => (item.body ?? "").includes(markerTag(marker)));
  }

  async function findMarkedComment(issueNumber, marker) {
    const comments = await listComments(issueNumber);
    return comments.find((item) => (item.body ?? "").includes(markerTag(marker)));
  }

  async function createComment(issueNumber, marker, body) {
    const commentBody = `${body}\n\n${markerTag(marker)}`;
    const created = await github.rest.issues.createComment({
      ...repoContext,
      issue_number: issueNumber,
      body: commentBody,
    });
    const comments = commentsCache.get(issueNumber);
    if (comments) {
      comments.push(created.data);
    }
  }

  async function upsertComment(issueNumber, marker, body) {
    const taggedBody = `${body}\n\n${markerTag(marker)}`;
    const existing = await findMarkedComment(issueNumber, marker);
    if (!existing) {
      await createComment(issueNumber, marker, body);
      return "created";
    }

    if ((existing.body ?? "") === taggedBody) {
      return "unchanged";
    }

    const updated = await github.rest.issues.updateComment({
      ...repoContext,
      comment_id: existing.id,
      body: taggedBody,
    });
    existing.body = updated.data.body;
    return "updated";
  }

  async function createCommentOnce(issueNumber, marker, body) {
    if (!marker) {
      await createComment(issueNumber, `adhoc-${Date.now()}`, body);
      return true;
    }
    if (await hasMarker(issueNumber, marker)) {
      return false;
    }
    await createComment(issueNumber, marker, body);
    return true;
  }

  async function removeLabel(issueNumber, name) {
    try {
      await github.rest.issues.removeLabel({
        ...repoContext,
        issue_number: issueNumber,
        name,
      });
    } catch (error) {
      if (error?.status !== 404) {
        throw error;
      }
    }
  }

  async function syncBugSubtypeLabel(currentIssue) {
    if (!labelSet.has("bug")) {
      return;
    }

    const selectedBugType = extractIssueFormValue(currentIssue.body ?? "", ["Bug type", "问题类型"]);
    const targetLabel = bugTypeToLabel[selectedBugType];
    if (!targetLabel) {
      return;
    }

    const targetSpec = bugSubtypeLabelSpecs[targetLabel];
    await ensureLabelExists(targetLabel, targetSpec.color, targetSpec.description);

    for (const subtypeLabel of bugSubtypeLabels) {
      if (subtypeLabel === targetLabel || !labelSet.has(subtypeLabel)) {
        continue;
      }
      await removeLabel(currentIssue.number, subtypeLabel);
      labelSet.delete(subtypeLabel);
    }

    if (!labelSet.has(targetLabel)) {
      await github.rest.issues.addLabels({
        ...repoContext,
        issue_number: currentIssue.number,
        labels: [targetLabel],
      });
      labelSet.add(targetLabel);
    }
  }

  async function syncIssueClassificationLabels(currentIssue) {
    const classificationConfig = config.issue_classification ?? {};
    const rules = classificationConfig.rules ?? [];
    if (rules.length === 0) {
      return;
    }

    const searchableText = normalizeText(`${currentIssue.title ?? ""}\n${currentIssue.body ?? ""}`);
    const maxLabelsToAdd = classificationConfig.max_labels_to_add ?? 3;
    const exclusiveGroups = new Map();
    for (const group of classificationConfig.exclusive_groups ?? []) {
      for (const label of group.labels ?? []) {
        exclusiveGroups.set(label, group.name);
      }
    }

    const occupiedGroups = new Set(
      Array.from(labelSet)
        .map((label) => exclusiveGroups.get(label))
        .filter(Boolean),
    );
    const matchedRules = [];

    for (const rule of rules) {
      if (labelSet.has(rule.label)) {
        continue;
      }
      const keywords = rule.keywords ?? [];
      const matchedKeywords = keywords.filter((keyword) => searchableText.includes(normalizeText(keyword)));
      if (matchedKeywords.length === 0) {
        continue;
      }

      matchedRules.push({
        ...rule,
        matchedKeywordCount: matchedKeywords.length,
      });
    }

    matchedRules.sort((left, right) => {
      const priorityDelta = (right.priority ?? 0) - (left.priority ?? 0);
      if (priorityDelta !== 0) {
        return priorityDelta;
      }

      const keywordDelta = right.matchedKeywordCount - left.matchedKeywordCount;
      if (keywordDelta !== 0) {
        return keywordDelta;
      }

      return left.label.localeCompare(right.label);
    });

    const labelsToAdd = [];
    for (const rule of matchedRules) {
      if (labelsToAdd.length >= maxLabelsToAdd) {
        break;
      }

      const groupName = rule.group ?? exclusiveGroups.get(rule.label);
      if (groupName && occupiedGroups.has(groupName)) {
        continue;
      }

      await ensureLabelExists(rule.label, rule.color, rule.description);
      labelsToAdd.push(rule.label);
      labelSet.add(rule.label);
      if (groupName) {
        occupiedGroups.add(groupName);
      }
    }

    if (labelsToAdd.length > 0) {
      await github.rest.issues.addLabels({
        ...repoContext,
        issue_number: currentIssue.number,
        labels: labelsToAdd,
      });
    }
  }

  async function isFirstRepositoryItem(login, kind, currentNumber) {
    if (!login) {
      return false;
    }

    const qualifier = kind === "pull_request" ? "is:pr" : "is:issue";
    const response = await github.rest.search.issuesAndPullRequests({
      q: `repo:${repoContext.owner}/${repoContext.repo} ${qualifier} author:${login}`,
      per_page: 5,
    });
    const otherItems = (response.data.items ?? []).filter((item) => item.number !== currentNumber);
    const totalCount = response.data.total_count ?? otherItems.length;
    return otherItems.length === 0 && totalCount <= 1;
  }

  function buildWelcomeBody(kind, isFirstTime) {
    const welcomeConfig = kind === "pull_request" ? config.welcome.pull_request : config.welcome.issue;
    const parts = [
      renderTemplate(welcomeConfig.body, {
        contributing: config.links.contributing,
      }),
    ];

    if (isFirstTime && welcomeConfig.first_time_suffix) {
      parts.push(welcomeConfig.first_time_suffix);
    }

    return parts.join("\n\n");
  }

  function findMissingIssueFields(currentIssue) {
    if (!currentIssue) {
      return [];
    }

    const body = currentIssue.body ?? "";
    const inferredKind = inferIssueTemplateKind(currentIssue);
    const requiredFieldSets = [
      {
        kind: "bug",
        labels: ["bug"],
        fields: [
          ["Summary", "问题描述"],
          ["Bug type", "问题类型"],
          ["Steps to reproduce", "复现步骤"],
          ["Expected behavior", "期望行为"],
          ["Actual behavior", "实际行为"],
          ["AISH version", "AISH 版本"],
          ["Operating system", "操作系统"],
          ["Install method", "安装方式"],
        ],
      },
      {
        kind: "feature",
        labels: ["enhancement", "feature-request"],
        fields: [
          ["Summary", "功能概述"],
          ["Use case", "使用场景"],
          ["Proposed solution", "期望方案"],
        ],
      },
    ];

    const selectedRule = requiredFieldSets.find(
      (rule) => rule.kind === inferredKind || rule.labels.some((name) => labelSet.has(name)),
    );
    if (!selectedRule) {
      return [];
    }

    return selectedRule.fields
      .filter((aliases) => !hasMeaningfulContent(extractIssueFormValue(body, aliases)))
      .map((aliases) => aliases[0]);
  }

  function inferIssueTemplateKind(currentIssue) {
    const title = normalizeText(currentIssue.title);
    const body = currentIssue.body ?? "";

    if (labelSet.has("bug") || title.startsWith("[bug]")) {
      return "bug";
    }
    if (labelSet.has("enhancement") || labelSet.has("feature-request") || title.startsWith("[feature]")) {
      return "feature";
    }
    if (
      extractIssueFormValue(body, ["Steps to reproduce", "复现步骤"]) ||
      extractIssueFormValue(body, ["Expected behavior", "期望行为"])
    ) {
      return "bug";
    }
    if (
      extractIssueFormValue(body, ["Use case", "使用场景"]) ||
      extractIssueFormValue(body, ["Proposed solution", "期望方案"])
    ) {
      return "feature";
    }
    return null;
  }

  function issueLooksUntemplated(currentIssue) {
    return !inferIssueTemplateKind(currentIssue);
  }

  function findMissingPullRequestSections(currentPullRequest) {
    if (!currentPullRequest) {
      return [];
    }

    const body = currentPullRequest.body ?? "";
    const missing = [];
    const requiredSections = [
      ["Summary", "概述"],
      ["User-visible Changes", "用户可见变更"],
      ["Compatibility", "兼容性"],
      ["Testing", "测试验证"],
    ];

    for (const aliases of requiredSections) {
      const sectionBody = aliases.map((heading) => extractMarkdownSection(body, heading)).find(Boolean) ?? "";
      if (!hasMeaningfulContent(sectionBody)) {
        missing.push(aliases[0]);
      }
    }

    const changeTypeSection = ["Change Type", "改动类型"]
      .map((heading) => extractMarkdownSection(body, heading))
      .find(Boolean) ?? "";
    if (!hasCheckedCheckbox(changeTypeSection)) {
      missing.push("Change Type");
    }

    const scopeSection = ["Scope", "涉及范围"]
      .map((heading) => extractMarkdownSection(body, heading))
      .find(Boolean) ?? "";
    if (!hasCheckedCheckbox(scopeSection)) {
      missing.push("Scope");
    }

    return missing;
  }

  async function maybeWarnIncompleteIssue(currentIssue) {
    const templateConfig = config.template_checks.issue;
    if (issueLooksUntemplated(currentIssue)) {
      await upsertComment(
        currentIssue.number,
        templateConfig.marker,
        renderTemplate(templateConfig.missing_template_body, {
          contributing: config.links.contributing,
        }),
      );
      return;
    }

    const missingFields = findMissingIssueFields(currentIssue);
    if (missingFields.length === 0) {
      if (await hasMarker(currentIssue.number, templateConfig.marker)) {
        await upsertComment(currentIssue.number, templateConfig.marker, templateConfig.resolved_body);
      }
      return;
    }

    await upsertComment(
      currentIssue.number,
      templateConfig.marker,
      renderTemplate(templateConfig.body, {
        missing_items: formatMissingItems(missingFields),
      }),
    );
  }

  async function maybeWarnIncompletePullRequest(currentPullRequest) {
    const templateConfig = config.template_checks.pull_request;
    const missingSections = findMissingPullRequestSections(currentPullRequest);
    if (missingSections.length === 0) {
      if (await hasMarker(currentPullRequest.number, templateConfig.marker)) {
        await upsertComment(currentPullRequest.number, templateConfig.marker, templateConfig.resolved_body);
      }
      return;
    }

    await upsertComment(
      currentPullRequest.number,
      templateConfig.marker,
      renderTemplate(templateConfig.body, {
        missing_items: formatMissingItems(missingSections),
      }),
    );
  }

  if (comment) {
    const authorLogin = comment.user?.login ?? "";
    if (comment.user?.type === "Bot" || authorLogin.endsWith("[bot]")) {
      return;
    }

    const mentions = (comment.body ?? "").match(mentionRegex) || [];
    if (mentions.length > config.spam.mention_threshold) {
      await createCommentOnce(
        target.number,
        config.spam.comment.marker,
        config.spam.comment.body.replace(
          "GitHub Discussions",
          `[GitHub Discussions](${config.links.discussions})`,
        ),
      );
    }
    return;
  }

  const action = context.payload.action;

  if (issue) {
    if (action === "opened") {
      const isFirstIssue = await isFirstRepositoryItem(issue.user?.login ?? "", "issue", issue.number);
      await createCommentOnce(issue.number, config.welcome.issue.marker, buildWelcomeBody("issue", isFirstIssue));
    }

    if (action === "opened" || action === "edited" || action === "reopened") {
      const issueText = `${issue.title ?? ""}\n${issue.body ?? ""}`.trim();
      const mentions = issueText.match(mentionRegex) || [];
      const authorLogin = issue.user?.login ?? "";

      if (mentions.length > config.spam.mention_threshold && authorLogin !== context.repo.owner) {
        await createCommentOnce(issue.number, config.spam.issue.marker, config.spam.issue.body);
      }

      await syncIssueClassificationLabels(issue);
      await syncBugSubtypeLabel(issue);
      await maybeWarnIncompleteIssue(issue);
    }

    const title = issue.title ?? "";
    if (title.toLowerCase().includes("security") && !labelSet.has(config.guards.security_label)) {
      await github.rest.issues.addLabels({
        ...repoContext,
        issue_number: issue.number,
        labels: [config.guards.security_label],
      });
      labelSet.add(config.guards.security_label);
    }
  }

  if (pullRequest && action === "opened") {
    const isFirstPullRequest = await isFirstRepositoryItem(
      pullRequest.user?.login ?? "",
      "pull_request",
      pullRequest.number,
    );
    await createCommentOnce(
      pullRequest.number,
      config.welcome.pull_request.marker,
      buildWelcomeBody("pull_request", isFirstPullRequest),
    );
  }

  if (pullRequest && (action === "opened" || action === "edited" || action === "reopened")) {
    await maybeWarnIncompletePullRequest(pullRequest);
  }

  const hasTriggerLabel = labelSet.has(config.guards.trigger_label);
  if (hasTriggerLabel) {
    labelSet.delete(config.guards.trigger_label);
    await removeLabel(target.number, config.guards.trigger_label);
  }

  const isLabelEvent = action === "labeled";
  if (!hasTriggerLabel && !isLabelEvent) {
    return;
  }

  if (pullRequest) {
    if (labelSet.has(config.guards.dirty_label) || labelSet.size > config.guards.pull_request_max_labels) {
      await createCommentOnce(
        pullRequest.number,
        config.guards.dirty_pull_request.marker,
        config.guards.dirty_pull_request.body,
      );
      await github.rest.issues.update({
        ...repoContext,
        issue_number: pullRequest.number,
        state: "closed",
      });
      return;
    }

    if (labelSet.has(config.guards.invalid_label)) {
      await github.rest.issues.update({
        ...repoContext,
        issue_number: pullRequest.number,
        state: "closed",
      });
      return;
    }
  }

  if (issue && labelSet.has(config.guards.invalid_label)) {
    await github.rest.issues.update({
      ...repoContext,
      issue_number: issue.number,
      state: "closed",
      state_reason: "not_planned",
    });
    return;
  }

  const rule = config.label_rules.find((item) => labelSet.has(item.label));
  if (!rule) {
    return;
  }

  await createCommentOnce(target.number, rule.marker, rule.body);

  if (rule.close) {
    await github.rest.issues.update({
      ...repoContext,
      issue_number: target.number,
      state: "closed",
      state_reason: "not_planned",
    });
  }

  if (rule.lock) {
    await github.rest.issues.lock({
      ...repoContext,
      issue_number: target.number,
      lock_reason: rule.lockReason ?? "resolved",
    });
  }
}

module.exports = {
  run,
};