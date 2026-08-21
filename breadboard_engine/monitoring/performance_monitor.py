"""
Performance monitoring infrastructure for tool calling systems.

Provides comprehensive metrics collection, analysis, and reporting for
different tool calling formats and strategies.
"""

from __future__ import annotations

import json
import time
import sqlite3
import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
import statistics
import logging

from ..dialects.enhanced_base_dialect import ToolCallExecutionMetrics, TaskType


@dataclass
class PerformanceSnapshot:
    """Snapshot of performance metrics at a point in time."""
    timestamp: float
    format: str
    model_id: str
    task_type: str
    success_rate: float
    avg_execution_time: float
    total_executions: int
    error_rate: float
    token_efficiency: float


@dataclass
class AlertRule:
    """Configuration for performance alerts."""
    name: str
    metric: str  # success_rate, execution_time, error_rate
    threshold: float
    comparison: str  # lt, gt, eq
    window_minutes: int = 60
    min_sample_size: int = 10
    enabled: bool = True


class PerformanceDatabase:
    """SQLite database for storing performance metrics."""
    
    def __init__(self, db_path: str = "tool_calling_performance.db"):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_database()
    
    def _init_database(self):
        """Initialize the performance database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    format TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    error_type TEXT,
                    execution_time REAL NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    timestamp REAL NOT NULL,
                    call_id TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    format TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    success_rate REAL NOT NULL,
                    avg_execution_time REAL NOT NULL,
                    total_executions INTEGER NOT NULL,
                    error_rate REAL NOT NULL,
                    token_efficiency REAL NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    rule_name TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    threshold REAL NOT NULL,
                    message TEXT NOT NULL,
                    acknowledged BOOLEAN DEFAULT FALSE
                )
            """)
            
            # Create indexes for better query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON execution_metrics(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_format ON execution_metrics(format)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON performance_snapshots(timestamp)")
            
            conn.commit()
    
    def store_execution_metric(self, metric: ToolCallExecutionMetrics):
        """Store an execution metric in the database."""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO execution_metrics 
                    (format, model_id, task_type, success, error_type, execution_time, 
                     token_count, timestamp, call_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    metric.format,
                    metric.model_id,
                    metric.task_type,
                    metric.success,
                    metric.error_type,
                    metric.execution_time,
                    metric.token_count,
                    metric.timestamp,
                    metric.call_id
                ))
                conn.commit()
    
    def get_metrics(self, 
                   format: str = None,
                   model_id: str = None,
                   task_type: str = None,
                   start_time: float = None,
                   end_time: float = None,
                   limit: int = None) -> List[ToolCallExecutionMetrics]:
        """Retrieve metrics from the database."""
        
        query = "SELECT * FROM execution_metrics WHERE 1=1"
        params = []
        
        if format:
            query += " AND format = ?"
            params.append(format)
        
        if model_id:
            query += " AND model_id = ?"
            params.append(model_id)
        
        if task_type:
            query += " AND task_type = ?"
            params.append(task_type)
        
        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)
        
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)
        
        query += " ORDER BY timestamp DESC"
        
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            
            metrics = []
            for row in rows:
                metrics.append(ToolCallExecutionMetrics(
                    format=row[1],
                    model_id=row[2],
                    task_type=row[3],
                    success=bool(row[4]),
                    error_type=row[5],
                    execution_time=row[6],
                    token_count=row[7],
                    timestamp=row[8],
                    call_id=row[9]
                ))
            
            return metrics
    
    def store_snapshot(self, snapshot: PerformanceSnapshot):
        """Store a performance snapshot."""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO performance_snapshots 
                    (timestamp, format, model_id, task_type, success_rate, 
                     avg_execution_time, total_executions, error_rate, token_efficiency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot.timestamp,
                    snapshot.format,
                    snapshot.model_id,
                    snapshot.task_type,
                    snapshot.success_rate,
                    snapshot.avg_execution_time,
                    snapshot.total_executions,
                    snapshot.error_rate,
                    snapshot.token_efficiency
                ))
                conn.commit()
    
    def cleanup_old_data(self, days_to_keep: int = 30):
        """Clean up old data to prevent database growth."""
        cutoff_time = time.time() - (days_to_keep * 24 * 3600)
        
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM execution_metrics WHERE timestamp < ?", (cutoff_time,))
                conn.execute("DELETE FROM performance_snapshots WHERE timestamp < ?", (cutoff_time,))
                conn.execute("DELETE FROM alerts WHERE timestamp < ? AND acknowledged = TRUE", (cutoff_time,))
                conn.commit()


class PerformanceAnalyzer:
    """Analyze performance data and generate insights."""
    
    def __init__(self, database: PerformanceDatabase):
        self.db = database
        self.logger = logging.getLogger(__name__)
    
    def calculate_success_rate(self, 
                             format: str = None,
                             model_id: str = None,
                             task_type: str = None,
                             hours: float = 24.0) -> float:
        """Calculate success rate for given parameters."""
        
        start_time = time.time() - (hours * 3600)
        metrics = self.db.get_metrics(
            format=format,
            model_id=model_id,
            task_type=task_type,
            start_time=start_time
        )
        
        if not metrics:
            return 0.0
        
        successes = sum(1 for m in metrics if m.success)
        return successes / len(metrics)
    
    def calculate_performance_trends(self, 
                                   format: str,
                                   hours: float = 168.0) -> Dict[str, List[Tuple[float, float]]]:
        """Calculate performance trends over time."""
        
        start_time = time.time() - (hours * 3600)
        metrics = self.db.get_metrics(format=format, start_time=start_time)
        
        # Group metrics by hour
        hourly_data = {}
        for metric in metrics:
            hour_bucket = int(metric.timestamp // 3600) * 3600
            if hour_bucket not in hourly_data:
                hourly_data[hour_bucket] = []
            hourly_data[hour_bucket].append(metric)
        
        # Calculate trends
        trends = {
            "success_rate": [],
            "execution_time": [],
            "error_rate": []
        }
        
        for hour_timestamp in sorted(hourly_data.keys()):
            hour_metrics = hourly_data[hour_timestamp]
            
            # Success rate
            successes = sum(1 for m in hour_metrics if m.success)
            success_rate = successes / len(hour_metrics) if hour_metrics else 0.0
            trends["success_rate"].append((hour_timestamp, success_rate))
            
            # Average execution time
            if hour_metrics:
                avg_time = sum(m.execution_time for m in hour_metrics) / len(hour_metrics)
                trends["execution_time"].append((hour_timestamp, avg_time))
            
            # Error rate
            error_rate = 1.0 - success_rate
            trends["error_rate"].append((hour_timestamp, error_rate))
        
        return trends
    
    def compare_formats(self, 
                       formats: List[str],
                       task_type: str = None,
                       hours: float = 24.0) -> Dict[str, Dict[str, float]]:
        """Compare performance between formats."""
        
        start_time = time.time() - (hours * 3600)
        comparison = {}
        
        for format in formats:
            metrics = self.db.get_metrics(
                format=format,
                task_type=task_type,
                start_time=start_time
            )
            
            if not metrics:
                comparison[format] = {
                    "success_rate": 0.0,
                    "avg_execution_time": 0.0,
                    "total_executions": 0,
                    "error_rate": 1.0
                }
                continue
            
            successes = sum(1 for m in metrics if m.success)
            success_rate = successes / len(metrics)
            avg_time = sum(m.execution_time for m in metrics) / len(metrics)
            
            comparison[format] = {
                "success_rate": success_rate,
                "avg_execution_time": avg_time,
                "total_executions": len(metrics),
                "error_rate": 1.0 - success_rate
            }
        
        return comparison
    
    def detect_performance_anomalies(self, 
                                   format: str,
                                   hours: float = 24.0) -> List[Dict[str, Any]]:
        """Detect performance anomalies."""
        
        start_time = time.time() - (hours * 3600)
        metrics = self.db.get_metrics(format=format, start_time=start_time)
        
        if len(metrics) < 10:  # Need minimum sample size
            return []
        
        anomalies = []
        
        # Calculate baseline metrics
        execution_times = [m.execution_time for m in metrics]
        avg_time = statistics.mean(execution_times)
        std_time = statistics.stdev(execution_times) if len(execution_times) > 1 else 0
        
        success_rate = sum(1 for m in metrics if m.success) / len(metrics)
        
        # Detect execution time anomalies (> 3 standard deviations)
        for metric in metrics:
            if std_time > 0 and abs(metric.execution_time - avg_time) > (3 * std_time):
                anomalies.append({
                    "type": "execution_time_outlier",
                    "timestamp": metric.timestamp,
                    "value": metric.execution_time,
                    "baseline": avg_time,
                    "severity": "high" if metric.execution_time > avg_time + (3 * std_time) else "medium"
                })
        
        # Detect success rate drops (rolling window)
        window_size = min(20, len(metrics) // 4)
        if window_size >= 5:
            for i in range(len(metrics) - window_size):
                window_metrics = metrics[i:i + window_size]
                window_success_rate = sum(1 for m in window_metrics if m.success) / len(window_metrics)
                
                if window_success_rate < success_rate * 0.7:  # 30% drop
                    anomalies.append({
                        "type": "success_rate_drop",
                        "timestamp": window_metrics[-1].timestamp,
                        "value": window_success_rate,
                        "baseline": success_rate,
                        "severity": "high" if window_success_rate < success_rate * 0.5 else "medium"
                    })
        
        return anomalies
    
    def generate_insights(self, format: str, hours: float = 168.0) -> Dict[str, Any]:
        """Generate performance insights for a format."""
        
        start_time = time.time() - (hours * 3600)
        metrics = self.db.get_metrics(format=format, start_time=start_time)
        
        if not metrics:
            return {"error": "No data available for the specified period"}
        
        # Basic metrics
        successes = sum(1 for m in metrics if m.success)
        success_rate = successes / len(metrics)
        avg_time = sum(m.execution_time for m in metrics) / len(metrics)
        
        # Error analysis
        error_types = {}
        for metric in metrics:
            if not metric.success and metric.error_type:
                error_types[metric.error_type] = error_types.get(metric.error_type, 0) + 1
        
        # Task type breakdown
        task_breakdown = {}
        for metric in metrics:
            task_breakdown[metric.task_type] = task_breakdown.get(metric.task_type, 0) + 1
        
        # Model performance
        model_performance = {}
        for metric in metrics:
            if metric.model_id not in model_performance:
                model_performance[metric.model_id] = {"successes": 0, "total": 0}
            model_performance[metric.model_id]["total"] += 1
            if metric.success:
                model_performance[metric.model_id]["successes"] += 1
        
        # Calculate model success rates
        for model_id in model_performance:
            perf = model_performance[model_id]
            perf["success_rate"] = perf["successes"] / perf["total"] if perf["total"] > 0 else 0.0
        
        insights = {
            "overall_performance": {
                "success_rate": success_rate,
                "total_executions": len(metrics),
                "avg_execution_time": avg_time,
                "error_rate": 1.0 - success_rate
            },
            "error_analysis": error_types,
            "task_type_breakdown": task_breakdown,
            "model_performance": model_performance,
            "trends": self.calculate_performance_trends(format, hours),
            "anomalies": self.detect_performance_anomalies(format, 24.0)
        }
        
        return insights


class AlertManager:
    """Manage performance alerts and notifications."""
    
    def __init__(self, database: PerformanceDatabase):
        self.db = database
        self.rules: List[AlertRule] = []
        self.logger = logging.getLogger(__name__)
    
    def add_alert_rule(self, rule: AlertRule):
        """Add an alert rule."""
        self.rules.append(rule)
        self.logger.info(f"Added alert rule: {rule.name}")
    
    def check_alerts(self, analyzer: PerformanceAnalyzer):
        """Check all alert rules and trigger alerts if needed."""
        
        for rule in self.rules:
            if not rule.enabled:
                continue
            
            try:
                self._check_rule(rule, analyzer)
            except Exception as e:
                self.logger.error(f"Error checking alert rule {rule.name}: {e}")
    
    def _check_rule(self, rule: AlertRule, analyzer: PerformanceAnalyzer):
        """Check a specific alert rule."""
        
        # Get recent metrics
        start_time = time.time() - (rule.window_minutes * 60)
        
        # Get all unique formats to check
        formats = ["unified_diff", "native_function_calling", "anthropic_xml", 
                  "json_block", "yaml_command", "xml_python_hybrid"]
        
        for format in formats:
            metrics = analyzer.db.get_metrics(format=format, start_time=start_time)
            
            if len(metrics) < rule.min_sample_size:
                continue
            
            # Calculate metric value
            if rule.metric == "success_rate":
                successes = sum(1 for m in metrics if m.success)
                value = successes / len(metrics)
            elif rule.metric == "execution_time":
                value = sum(m.execution_time for m in metrics) / len(metrics)
            elif rule.metric == "error_rate":
                errors = sum(1 for m in metrics if not m.success)
                value = errors / len(metrics)
            else:
                continue
            
            # Check threshold
            should_alert = False
            if rule.comparison == "lt" and value < rule.threshold:
                should_alert = True
            elif rule.comparison == "gt" and value > rule.threshold:
                should_alert = True
            elif rule.comparison == "eq" and abs(value - rule.threshold) < 0.01:
                should_alert = True
            
            if should_alert:
                self._trigger_alert(rule, format, rule.metric, value, rule.threshold)
    
    def _trigger_alert(self, rule: AlertRule, format: str, metric: str, value: float, threshold: float):
        """Trigger an alert."""
        
        message = f"Alert: {rule.name} - {format} {metric} is {value:.3f} (threshold: {threshold:.3f})"
        
        # Store alert in database
        with sqlite3.connect(self.db.db_path) as conn:
            conn.execute("""
                INSERT INTO alerts (timestamp, rule_name, metric, value, threshold, message)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (time.time(), rule.name, metric, value, threshold, message))
            conn.commit()
        
        # Log alert
        self.logger.warning(message)
        
        # Could add additional notification mechanisms here (email, Slack, etc.)


class PerformanceMonitor:
    """Main performance monitoring class."""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        
        # Initialize components
        db_path = self.config.get("database_path", "tool_calling_performance.db")
        self.database = PerformanceDatabase(db_path)
        self.analyzer = PerformanceAnalyzer(self.database)
        self.alert_manager = AlertManager(self.database)
        
        # Setup default alert rules
        self._setup_default_alerts()
        
        # Background tasks
        self._last_cleanup = time.time()
        self._last_snapshot = time.time()
        
        self.logger = logging.getLogger(__name__)
    
    def _setup_default_alerts(self):
        """Setup default alert rules."""
        
        # Low success rate alert
        self.alert_manager.add_alert_rule(AlertRule(
            name="low_success_rate",
            metric="success_rate",
            threshold=0.5,
            comparison="lt",
            window_minutes=60,
            min_sample_size=20
        ))
        
        # High error rate alert
        self.alert_manager.add_alert_rule(AlertRule(
            name="high_error_rate",
            metric="error_rate",
            threshold=0.3,
            comparison="gt",
            window_minutes=30,
            min_sample_size=10
        ))
        
        # Slow execution alert
        self.alert_manager.add_alert_rule(AlertRule(
            name="slow_execution",
            metric="execution_time",
            threshold=10.0,  # 10 seconds
            comparison="gt",
            window_minutes=60,
            min_sample_size=10
        ))
    
    def record_execution(self, metric: ToolCallExecutionMetrics):
        """Record a tool execution metric."""
        self.database.store_execution_metric(metric)
        
        # Periodic maintenance
        self._periodic_maintenance()
    
    def get_performance_summary(self, hours: float = 24.0) -> Dict[str, Any]:
        """Get performance summary for all formats."""
        
        formats = ["unified_diff", "native_function_calling", "anthropic_xml", 
                  "json_block", "yaml_command", "xml_python_hybrid"]
        
        summary = {
            "timestamp": time.time(),
            "period_hours": hours,
            "formats": {}
        }
        
        for format in formats:
            insights = self.analyzer.generate_insights(format, hours)
            summary["formats"][format] = insights
        
        return summary
    
    def compare_formats(self, formats: List[str], task_type: str = None, hours: float = 24.0) -> Dict[str, Any]:
        """Compare performance between formats."""
        return self.analyzer.compare_formats(formats, task_type, hours)
    
    def _periodic_maintenance(self):
        """Perform periodic maintenance tasks."""
        current_time = time.time()
        
        # Cleanup old data (daily)
        if current_time - self._last_cleanup > 24 * 3600:
            self.database.cleanup_old_data()
            self._last_cleanup = current_time
        
        # Take performance snapshots (hourly)
        if current_time - self._last_snapshot > 3600:
            self._take_performance_snapshots()
            self._last_snapshot = current_time
        
        # Check alerts (every 5 minutes)
        if hasattr(self, '_last_alert_check'):
            if current_time - self._last_alert_check > 300:
                self.alert_manager.check_alerts(self.analyzer)
                self._last_alert_check = current_time
        else:
            self._last_alert_check = current_time
    
    def _take_performance_snapshots(self):
        """Take periodic performance snapshots."""
        
        formats = ["unified_diff", "native_function_calling", "anthropic_xml", 
                  "json_block", "yaml_command", "xml_python_hybrid"]
        
        current_time = time.time()
        
        for format in formats:
            metrics = self.database.get_metrics(format=format, start_time=current_time - 3600)
            
            if not metrics:
                continue
            
            successes = sum(1 for m in metrics if m.success)
            success_rate = successes / len(metrics)
            avg_time = sum(m.execution_time for m in metrics) / len(metrics)
            error_rate = 1.0 - success_rate
            
            # Calculate token efficiency (tokens per successful execution)
            successful_metrics = [m for m in metrics if m.success]
            token_efficiency = (sum(m.token_count for m in successful_metrics) / len(successful_metrics)) if successful_metrics else 0.0
            
            snapshot = PerformanceSnapshot(
                timestamp=current_time,
                format=format,
                model_id="aggregate",  # Aggregate across all models
                task_type="aggregate",  # Aggregate across all task types
                success_rate=success_rate,
                avg_execution_time=avg_time,
                total_executions=len(metrics),
                error_rate=error_rate,
                token_efficiency=token_efficiency
            )
            
            self.database.store_snapshot(snapshot)