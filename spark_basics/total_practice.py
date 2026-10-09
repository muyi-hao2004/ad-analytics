df = spark.read.csv(sample_ad_data.csv)

df.filter((F.col("cost") > 1000) & (F.col("clicks") > 50)).orderBy(F.desc("cost")).show(10)

df.withColumn(
    "ctr", F.when(F.col("impression") > 0, F.col("clicks")/F.col("impressions")).otherwise(0),
    "cpa", F.when(F.col("conversions") > 0, F.col("cost")/F.col("conversions")).otherwise(0)
)

df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost"),
    F.sum("clicks").alias("total_clicks"),
    F.sum("conversions").alias("total_conversions"),
    F.avg("cost").alias("avg_cost")
).orderBy(F.desc("total_cost"))

df.groupBy("channel").agg(
    F.sum("cost").alias("total_cost")
).filter(F.col("total_cost") > 15000).orderBy(F.desc("total_cost"))

df.groupBy("channel", "date").agg(
    F.sum("cost").alias("total_cost")
).orderBy("channel" ,F.desc("cost")).show(10)

df.join(df_channel, on = "channel", how = "left").show(10)

window = (Window.patitionBy("channel").orderBy(F.col("total_cost")))
df.groupBy("channel", "campaign").agg(
    F.sum("cost").alias("total_cost")
).withcolumn("rank", F.row_number().over(window)).filter(F.col("rank") < 4)

window_spec = (Window.orderBy("date").rowsBetween(Window.unboundedPreceding, Window.currentRow)
df.groupBy("date").agg(
    F.sum("cost").alias("total_cost")
).withcolumn("accumulate" F.sum("cost").over(window_spec))

window_1 = (Window.orderBy("date")
df.groupBy("date").agg(
    F.sum("cost").alias("total_cost")
).withcolumn("huanbi", (F.col("cost") - F.lag("cost", 1))/F.lag("cost", 1))

df1 = df.groupBy("channel", "date").agg(
    F.sum("impreessions").alias("total_im"),
    F.sum("clicks").alias("total_cl"),
    F.sum("cost").alias("total_cost")
)
df2 = df1.withcolumn(
    "ctr", F.when(F.col("impressions") > 0, F.col("clicks")/F.col("impressions")).otherwise(0),
    "cvr", F.when(F.col("clicks") > 0, F.col("conversions")/F.col("clicks")).otherwise(0)
)

window_2 = (Window.patitionBy("channel").orderBy("cost"))
df2.withcolumn("rank", F.row_number().over(window_2)).filter(F.col("rank") == 1).show()


